"""Builds the DRI seed files from Health Canada's published DRI tables.

Nothing in the seed files is typed by hand: this module parses the official HTML pages and
writes ``seeds/dri_values.csv``, ``seeds/amdr.csv`` and ``seeds/eer_equations.csv``. Values the
source marks "ND" (not determinable), and rows missing from the source tables, are written
with an empty amount and a status flag, never filled in.

Run with ``nutrition build-dri-seed`` (fetches the pages) or ``--html-dir`` for saved copies.
"""

import csv
import re
import ssl
import time
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

BASE_URL = (
    "https://www.canada.ca/en/health-canada/services/food-nutrition/healthy-eating/"
    "dietary-reference-intakes/tables"
)
PAGES = {
    "vitamins": "reference-values-vitamins",
    "elements": "reference-values-elements",
    "macronutrients": "reference-values-macronutrients",
    "energy": "equations-estimate-energy-requirement",
}


def page_url(page: str) -> str:
    return f"{BASE_URL}/{PAGES[page]}.html"


# --------------------------------------------------------------------------------------------
# HTML table parsing


@dataclass
class Cell:
    text: str
    footnotes: list[str]  # footnote anchor ids, e.g. "t2fn18"
    markers: list[str]  # visible footnote markers, e.g. "*", "18", "g"
    colspan: int = 1
    rowspan: int = 1


@dataclass
class Table:
    caption: str
    rows: list[list[Cell]] = field(default_factory=list)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[Table] = []
        self.footnotes: dict[str, str] = {}
        self.page_date: str | None = None
        self._table: Table | None = None
        self._row: list[Cell] | None = None
        self._cell: dict | None = None
        self._caption: list[str] | None = None
        self._fn_link: dict | None = None
        self._dd_id: str | None = None
        self._dd_text: list[str] = []
        self._in_return_link = 0
        self._in_time = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "table":
            self._table = Table(caption="")
        elif tag == "caption" and self._table is not None:
            self._caption = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = {
                "text": [],
                "footnotes": [],
                "markers": [],
                "colspan": int(a.get("colspan") or 1),
                "rowspan": int(a.get("rowspan") or 1),
            }
        elif tag == "a" and "fn-lnk" in cls:
            self._fn_link = {"id": (a.get("href") or "").lstrip("#"), "text": []}
        elif tag == "dd" and a.get("id"):
            self._dd_id, self._dd_text = a["id"], []
        elif tag == "p" and self._dd_id and "fn-rtn" in cls:
            self._in_return_link += 1
        elif tag == "time":
            self._in_time = True
        elif tag == "br":
            self._data(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None
        elif tag == "caption" and self._caption is not None and self._table is not None:
            self._table.caption = " ".join("".join(self._caption).split())
            self._caption = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.rows.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            c = self._cell
            self._row.append(
                Cell(
                    " ".join("".join(c["text"]).split()),
                    c["footnotes"],
                    c["markers"],
                    c["colspan"],
                    c["rowspan"],
                )
            )
            self._cell = None
        elif tag == "a" and self._fn_link is not None:
            link, self._fn_link = self._fn_link, None
            # The link text is "Table N footnote X"; X is the visible marker.
            marker = "".join(link["text"]).split("footnote")[-1].strip()
            if self._cell is not None:
                self._cell["footnotes"].append(link["id"])
                self._cell["markers"].append(marker)
        elif tag == "dd" and self._dd_id:
            self.footnotes[self._dd_id] = " ".join("".join(self._dd_text).split())
            self._dd_id = None
        elif tag == "p" and self._in_return_link:
            self._in_return_link -= 1
        elif tag == "time":
            self._in_time = False

    def handle_data(self, data: str) -> None:
        if self._in_time and re.fullmatch(r"\s*\d{4}-\d{2}-\d{2}\s*", data):
            self.page_date = data.strip()
        self._data(data)

    def _data(self, data: str) -> None:
        if self._fn_link is not None:
            self._fn_link["text"].append(data)
        elif self._cell is not None:
            self._cell["text"].append(data)
        elif self._caption is not None:
            self._caption.append(data)
        if self._dd_id and not self._in_return_link:
            self._dd_text.append(data)


@dataclass
class Page:
    tables: list[Table]
    footnotes: dict[str, str]
    date: str | None
    text_lines: list[str]


def parse_page(html: str) -> Page:
    p = _PageParser()
    p.feed(html)
    date = p.page_date
    if date is None:
        m = re.search(r"Date modified:?\s*</dt>\s*<dd>\s*<time>(\d{4}-\d{2}-\d{2})", html)
        date = m.group(1) if m else None
    return Page(p.tables, p.footnotes, date, _visible_lines(html))


def _visible_lines(html: str) -> list[str]:
    import html as html_mod

    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    t = re.sub(r"<(br|/p|/h\d|/li|/dt|/dd|/tr|/caption)[^>]*>", "\n", t)
    t = html_mod.unescape(re.sub(r"<[^>]+>", " ", t))
    return [" ".join(line.split()) for line in t.splitlines() if line.strip()]


# --------------------------------------------------------------------------------------------
# Life-stage groups

SECTIONS = {"Infants", "Children", "Males", "Females", "Pregnancy", "Lactation"}

# (section, age label) -> (sex, life_stage, age_min_months, age_max_months [exclusive])
# Infant groups are 0-5.9 and 6-11.9 months per the source's footnote "a".
EXPECTED_GROUPS: dict[tuple[str, str], tuple[str, str, int, int | None]] = {
    ("Infants", "0-6 mo"): ("any", "general", 0, 6),
    ("Infants", "7-12 mo"): ("any", "general", 6, 12),
    ("Children", "1-3 y"): ("any", "general", 12, 48),
    ("Children", "4-8 y"): ("any", "general", 48, 108),
    **{
        (sec, label): (sex, "general", lo, hi)
        for sec, sex in (("Males", "M"), ("Females", "F"))
        for label, lo, hi in (
            ("9-13 y", 108, 168),
            ("14-18 y", 168, 228),
            ("19-30 y", 228, 372),
            ("31-50 y", 372, 612),
            ("51-70 y", 612, 852),
            (">70 y", 852, None),
        )
    },
    **{
        (sec, label): ("F", stage, lo, hi)
        for sec, stage in (("Pregnancy", "pregnancy"), ("Lactation", "lactation"))
        for label, lo, hi in (("<=18 y", 168, 228), ("19-30 y", 228, 372), ("31-50 y", 372, 612))
    },
}


def normalize_age_label(label: str) -> str:
    s = label.replace("–", "-").replace("—", "-").replace("≤", "<=")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("<= ", "<=")
    return s


# --------------------------------------------------------------------------------------------
# Nutrient extraction spec


@dataclass(frozen=True)
class Spec:
    page: str
    caption_contains: str
    group_startswith: str
    nutrient_key: str
    sub_unit_contains: str | None = None  # pick sub-columns whose header mentions this unit


SPECS = [
    Spec("vitamins", "Vitamin D", "Vitamin D", "vitamin_d", sub_unit_contains="μg"),
    Spec("vitamins", "Vitamin C", "Vitamin C", "vitamin_c"),
    Spec("vitamins", "Folate", "Folate", "folate_dfe"),
    Spec("vitamins", "Folate", "Vitamin B12", "vitamin_b12"),
    Spec("elements", "Calcium", "Calcium", "calcium"),
    Spec("elements", "Iron", "Iron", "iron"),
    Spec("elements", "Zinc", "Zinc", "zinc"),
    Spec("elements", "Sodium", "Sodium", "sodium"),
    Spec("macronutrients", "Carbohydrate", "Carbohydrate", "carbohydrate"),
    Spec("macronutrients", "Carbohydrate", "Total Protein", "protein"),
    Spec("macronutrients", "Carbohydrate", "Total Fat", "fat"),
    Spec("macronutrients", "Carbohydrate", "α-linolenic", "omega3_ala"),
    Spec("macronutrients", "Carbohydrate", "Total Fibre", "fibre"),
]

VALUE_TYPES = ("RDA/AI", "EAR", "UL", "AI", "CDRR")

DRI_FIELDS = [
    "nutrient_key",
    "sex",
    "life_stage",
    "age_min_months",
    "age_max_months",
    "age_label",
    "value_type",
    "amount",
    "unit",
    "per_kg_body_weight",
    "status",
    "footnotes",
    "source_table",
    "source_url",
    "source_date",
]


def _columns(table: Table) -> list[tuple[Cell, Cell]]:
    """Pair each data column with its (group header, sub header) cells."""
    group_row, sub_row = table.rows[0], table.rows[1]
    groups: list[Cell] = []
    for c in group_row[1:]:  # skip the "Age" column
        groups.extend([c] * c.colspan)
    return list(zip(groups, sub_row, strict=True))


def _unit(group: Cell, sub: Cell) -> tuple[str, bool]:
    text = f"{group.text} {sub.text}"
    per_kg = "g/kg/day" in text
    for pattern, unit in (
        (r"μg/day|µg/day", "µg"),
        (r"mg/day", "mg"),
        (r"g/kg/day|g/day", "g"),
        (r"IU/day", "IU"),
        (r"Litres/day", "L"),
    ):
        if re.search(pattern, sub.text):
            return unit, per_kg
    for pattern, unit in ((r"μg/day|µg/day", "µg"), (r"mg/day", "mg"), (r"g/day", "g")):
        if re.search(pattern, group.text):
            return unit, per_kg
    return "", per_kg


def _parse_amount(text: str) -> float | None:
    t = text.replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def extract_dri_values(pages: dict[str, Page], specs: list[Spec] = SPECS) -> list[dict]:
    rows: list[dict] = []
    for spec in specs:
        page = pages[spec.page]
        table = next(t for t in page.tables if spec.caption_contains in t.caption)
        cols = [
            (i, g, s)
            for i, (g, s) in enumerate(_columns(table))
            if g.text.startswith(spec.group_startswith)
            and (spec.sub_unit_contains is None or spec.sub_unit_contains in s.text)
        ]
        if not cols:
            raise ValueError(f"no columns for {spec}")
        section = None
        seen_groups: set[tuple[str, str]] = set()
        for row in table.rows[2:]:
            if len(row) == 1 and row[0].text in SECTIONS:
                section = row[0].text
                continue
            if len(row) < 2 or section is None:
                continue  # footnote rows etc.
            label = normalize_age_label(row[0].text)
            key = (section, label)
            if key not in EXPECTED_GROUPS:
                raise ValueError(f"unexpected life-stage row {key} in {table.caption!r}")
            seen_groups.add(key)
            for i, group, sub in cols:
                rows.append(_value_row(spec, page, table, key, group, sub, row[i + 1]))
        for key in EXPECTED_GROUPS:
            if key not in seen_groups:
                for _i, group, sub in cols:
                    rows.append(_value_row(spec, page, table, key, group, sub, None))
    return rows


def _value_row(spec, page, table, key, group: Cell, sub: Cell, cell: Cell | None) -> dict:
    sex, stage, lo, hi = EXPECTED_GROUPS[key]
    vtype = next(v for v in VALUE_TYPES if sub.text.startswith(v))
    unit, per_kg = _unit(group, sub)
    amount = status = None
    fn_ids = [*group.footnotes, *sub.footnotes]
    if cell is None:
        status = "missing_from_source"
    else:
        fn_ids += cell.footnotes
        amount = _parse_amount(cell.text)
        if vtype == "RDA/AI":
            vtype = "AI" if "*" in cell.markers else "RDA"
        if amount is not None:
            status = "ok"
        elif cell.text.upper().startswith("ND"):
            status = "not_determinable"
        else:
            status = f"unparsed:{cell.text}"
    if vtype == "RDA/AI":  # missing row: type can't be told apart
        vtype = "RDA_or_AI"
    notes = []
    for fid in dict.fromkeys(fn_ids):
        text = page.footnotes.get(fid, "")
        # The generic "this table presents EARs, RDAs..." note adds nothing per value.
        if text and not text.startswith("This table presents"):
            notes.append(text)
    return {
        "nutrient_key": spec.nutrient_key,
        "sex": sex,
        "life_stage": stage,
        "age_min_months": lo,
        "age_max_months": "" if hi is None else hi,
        "age_label": f"{key[0]} {key[1]}",
        "value_type": vtype,
        "amount": "" if amount is None else _fmt(amount),
        "unit": unit,
        "per_kg_body_weight": "yes" if per_kg else "no",
        "status": status,
        "footnotes": " | ".join(notes),
        "source_table": table.caption,
        "source_url": page_url(spec.page),
        "source_date": page.date or "",
    }


def _fmt(x: float) -> str:
    return f"{x:g}"


# --------------------------------------------------------------------------------------------
# AMDR

AMDR_FIELDS = [
    "nutrient_key",
    "age_min_months",
    "age_max_months",
    "age_label",
    "low_pct",
    "high_pct",
    "source_url",
    "source_date",
]
AMDR_AGES = {
    "1–3 years": (12, 48),
    "4–18 years": (48, 228),
    "19 years and over": (228, None),
}
AMDR_COLUMNS = {
    "Total Carbohydrate": "carbohydrate",
    "Total Protein": "protein",
    "Total Fat": "fat",
}


def extract_amdr(page: Page) -> list[dict]:
    table = next(t for t in page.tables if "Acceptable Macronutrient" in t.caption)
    header = table.rows[0]
    out = []
    for row in table.rows[1:]:
        label = row[0].text
        if label not in AMDR_AGES:
            continue
        lo, hi = AMDR_AGES[label]
        for h, cell in zip(header[1:], row[1:], strict=False):
            key = next((v for k, v in AMDR_COLUMNS.items() if h.text.startswith(k)), None)
            m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[–-]\s*(\d+(?:\.\d+)?)%", cell.text)
            if key and m:
                out.append(
                    {
                        "nutrient_key": key,
                        "age_min_months": lo,
                        "age_max_months": "" if hi is None else hi,
                        "age_label": label,
                        "low_pct": m.group(1),
                        "high_pct": m.group(2),
                        "source_url": page_url("macronutrients"),
                        "source_date": page.date or "",
                    }
                )
    return out


# --------------------------------------------------------------------------------------------
# Estimated Energy Requirement equations

EER_FIELDS = [
    "life_stage",
    "sex",
    "age_min_months",
    "age_max_months",
    "postpartum_min_months",
    "postpartum_max_months",
    "pre_pregnancy_bmi",
    "pa_cat",
    "constant",
    "age_years_coef",
    "height_cm_coef",
    "weight_kg_coef",
    "gestation_weeks_coef",
    "equation_text",
    "source_url",
    "source_date",
]

_AGE_HEADINGS = [
    (r"Age 0 to <3 months", 0, 3),
    (r"Age 3 to <6 months", 3, 6),
    (r"Age 6 to <12 months", 6, 12),
    (r"Age 1 year to < ?3 years", 12, 36),
    (r"Age 3 years to < ?4 years", 36, 48),
    (r"Age 4 years to < ?9 years", 48, 108),
    (r"Age 9 years to < ?14 years", 108, 168),
    (r"Age 14 years to < ?19 years", 168, 228),
    (r"Ages less than 19 years", 0, 228),
    (r"Ages 19 years and older", 228, None),
]
_PA = {"Inactive": "inactive", "Low active": "low_active", "Active": "active",
       "Very active": "very_active"}  # fmt: skip


def parse_eer_equation(text: str) -> dict[str, float]:
    s = text.replace("–", "-").replace("−", "-").replace("×", "*").replace(",", "")
    s = s.split("=", 1)[1]
    coefs = {"constant": 0.0, "age": 0.0, "height": 0.0, "weight": 0.0, "gestation": 0.0}
    for sign, num, var in re.findall(
        r"([+-]?)\s*\(?\s*(\d+(?:\.\d+)?)\s*(?:\*\s*(age|height|weight|gestation)[^)]*\))?", s
    ):
        value = float(num) * (-1 if sign == "-" else 1)
        coefs[var or "constant"] += value
    return coefs


def extract_eer(page: Page) -> list[dict]:
    lines = page.text_lines
    # Equations start after the PA CAT definitions table; skip the "On this page" list.
    start = next(i for i, ln in enumerate(lines) if ln.startswith("EER equations for infants"))
    stage = "general"
    sex = "any"
    age: tuple[int, int | None] = (0, None)
    postpartum: tuple[str, str] = ("", "")
    bmi = ""
    pa = "any"
    out = []
    for ln in lines[start - 2 :]:
        if ln.startswith("Page details"):
            break
        if ln.startswith("Children 0 to 3 years"):
            stage, sex, pa = "general", "any", "any"
        elif ln.startswith("Children and adolescents") or ln.startswith("Adults 19 years"):
            stage, pa = "general", "any"
            if ln.startswith("Adults"):
                age = (228, None)
        elif ln.startswith("Pregnancy second and third trimester"):
            stage, sex, age, postpartum = "pregnancy", "F", (0, None), ("", "")
        elif m := re.match(r"Breastfeeding, (\d+) to (\d+) months postpartum", ln):
            # Stored as written: inclusive ranges of completed months postpartum.
            stage, sex, postpartum = "lactation", "F", (m.group(1), m.group(2))
        elif m := re.match(r"(Underweight|Normal weight|Overweight|Obese) pre-pregnancy BMI", ln):
            bmi = m.group(1).lower().replace(" ", "_")
        elif ln in ("Males", "Females"):
            sex = ln[0]
        elif m := re.match(r"PA CAT (.+)$", ln):
            pa = _PA[m.group(1).strip()]
        else:
            for pattern, lo, hi in _AGE_HEADINGS:
                if re.match(pattern, ln):
                    age = (lo, hi)
                    break
            if ln.startswith("EER ="):
                c = parse_eer_equation(ln)
                out.append(
                    {
                        "life_stage": stage,
                        "sex": sex,
                        "age_min_months": age[0],
                        "age_max_months": "" if age[1] is None else age[1],
                        "postpartum_min_months": postpartum[0] if stage == "lactation" else "",
                        "postpartum_max_months": postpartum[1] if stage == "lactation" else "",
                        "pre_pregnancy_bmi": bmi if stage == "pregnancy" else "",
                        "pa_cat": pa,
                        "constant": _fmt(round(c["constant"], 4)),
                        "age_years_coef": _fmt(c["age"]),
                        "height_cm_coef": _fmt(c["height"]),
                        "weight_kg_coef": _fmt(c["weight"]),
                        "gestation_weeks_coef": _fmt(c["gestation"]),
                        "equation_text": ln,
                        "source_url": page_url("energy"),
                        "source_date": page.date or "",
                    }
                )
    return out


# --------------------------------------------------------------------------------------------
# Entry point


def fetch(url: str, attempts: int = 5) -> str:
    ctx = ssl.create_default_context()
    # canada.ca's CDN drops connections from non-browser user agents.
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
        "Accept": "text/html,*/*",
        "Accept-Language": "en-CA,en;q=0.8",
    }
    req = urllib.request.Request(url, headers=headers)
    delay = 2
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=120) as r:
                return r.read().decode("utf-8")
        except OSError:
            if i == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


def load_pages(html_dir: Path | None) -> dict[str, Page]:
    pages = {}
    for key, slug in PAGES.items():
        if html_dir:
            html = (html_dir / f"{slug}.html").read_text(encoding="utf-8")
        else:
            html = fetch(page_url(key))
        pages[key] = parse_page(html)
    return pages


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def build(out_dir: Path, html_dir: Path | None = None) -> dict[str, list[dict]]:
    pages = load_pages(html_dir)
    result = {
        "dri_values": extract_dri_values(pages),
        "amdr": extract_amdr(pages["macronutrients"]),
        "eer_equations": extract_eer(pages["energy"]),
    }
    write_csv(out_dir / "dri_values.csv", DRI_FIELDS, result["dri_values"])
    write_csv(out_dir / "amdr.csv", AMDR_FIELDS, result["amdr"])
    write_csv(out_dir / "eer_equations.csv", EER_FIELDS, result["eer_equations"])
    return result
