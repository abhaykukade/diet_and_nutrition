import csv

import pytest
from sqlalchemy import select

from nutrition.importers.base import SEEDS_DIR
from nutrition.models import Amdr, DriValue, EerEquation, Nutrient
from nutrition.seeds.load import load_reference_data
from nutrition.sources.hc_dri import (
    EXPECTED_GROUPS,
    Spec,
    extract_dri_values,
    parse_eer_equation,
    parse_page,
)

# Synthetic page in the same markup as canada.ca's DRI tables. Values are made up.
SAMPLE_HTML = """
<table><caption>Reference values for Iron and Things</caption>
<tr><th rowspan="2">Age</th>
    <th colspan="3">Iron<sup><a class="fn-lnk" href="#t2fn18"><span>Table 2 footnote </span>18</a>
      </sup><br>mg/day</th>
    <th colspan="1">Other</th></tr>
<tr><th>EAR</th><th>RDA/AI</th><th>UL</th><th>AI</th></tr>
<tr><td colspan="5">Infants</td></tr>
<tr><td>7&#8211;12 mo</td><td>ND</td>
    <td>1.5<sup><a class="fn-lnk" href="#t2fn*"><span>Table 2 footnote </span>*</a></sup></td>
    <td>40</td><td>9</td></tr>
<tr><td colspan="5">Males</td></tr>
<tr><td>19&#8211;30 y</td><td>6</td><td>8</td><td>1,045</td><td>9</td></tr>
</table>
<dl><dd id="t2fn18"><p>Iron footnote text.</p>
  <p class="fn-rtn"><a href="#x">Return to footnote 18 referrer</a></p></dd>
<dd id="t2fn*"><p>This table presents EARs, RDAs and AIs.</p></dd></dl>
<dl><dt>Date modified:</dt><dd><time>2025-11-19</time></dd></dl>
"""


@pytest.fixture
def sample_rows():
    page = parse_page(SAMPLE_HTML)
    spec = Spec("elements", "Iron", "Iron", "iron")
    return extract_dri_values({"elements": page}, [spec])


def _get(rows, label, vtype):
    return next(r for r in rows if r["age_label"] == label and r["value_type"] == vtype)


def test_ai_marker_nd_and_thousands_separator(sample_rows):
    assert _get(sample_rows, "Infants 7-12 mo", "AI")["amount"] == "1.5"
    assert _get(sample_rows, "Infants 7-12 mo", "EAR")["status"] == "not_determinable"
    assert _get(sample_rows, "Infants 7-12 mo", "EAR")["amount"] == ""
    assert _get(sample_rows, "Males 19-30 y", "RDA")["amount"] == "8"
    assert _get(sample_rows, "Males 19-30 y", "UL")["amount"] == "1045"


def test_only_matching_columns_are_extracted(sample_rows):
    assert {r["nutrient_key"] for r in sample_rows} == {"iron"}
    assert all(r["unit"] == "mg" for r in sample_rows)


def test_footnotes_and_source_are_attached(sample_rows):
    r = _get(sample_rows, "Males 19-30 y", "RDA")
    assert r["footnotes"] == "Iron footnote text."  # generic table note dropped
    assert r["source_date"] == "2025-11-19"
    assert r["source_url"].startswith("https://www.canada.ca/")


def test_rows_absent_from_source_are_flagged_not_filled(sample_rows):
    missing = [r for r in sample_rows if r["status"] == "missing_from_source"]
    # Every expected life-stage group except the two present, three columns each.
    assert len(missing) == (len(EXPECTED_GROUPS) - 2) * 3
    assert all(r["amount"] == "" for r in missing)


def test_parse_eer_equation():
    c = parse_eer_equation(
        "EER = 584.90 – (7.01 × age [y]) + (5.72 × height [cm]) + (11.71 × weight [kg]) + 540 – 140"
    )
    assert c == pytest.approx(
        {"constant": 984.9, "age": -7.01, "height": 5.72, "weight": 11.71, "gestation": 0}
    )
    c = parse_eer_equation(
        "EER = 1,131.20 – (2.04 × age [y]) + (0.34 × height [cm]) + (12.15 × weight [kg])"
        " + (9.16 × gestation [weeks]) – 50"
    )
    assert c["constant"] == pytest.approx(1081.2) and c["gestation"] == 9.16


# ---- the committed seed files -----------------------------------------------------------


def test_seed_files_have_sources_and_known_statuses():
    with (SEEDS_DIR / "dri_values.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows
    assert all(r["source_url"].startswith("https://www.canada.ca/") for r in rows)
    assert {r["status"] for r in rows} <= {"ok", "not_determinable", "missing_from_source"}
    assert all((r["amount"] == "") == (r["status"] != "ok") for r in rows)


def _dri(session, key, label, vtype, per_kg=False):
    return session.scalar(
        select(DriValue.amount)
        .join(Nutrient)
        .where(
            Nutrient.key == key,
            DriValue.age_label == label,
            DriValue.value_type == vtype,
            DriValue.per_kg_body_weight == per_kg,
        )
    )


def test_load_reference_data_spot_checks(session):
    counts = load_reference_data(session)
    assert counts["dri_values"] > 0 and counts["eer_equations"] > 0
    # Regression checks against values on Health Canada's DRI tables (page dated 2025-11-19).
    assert _dri(session, "iron", "Females 19-30 y", "RDA") == 18
    assert _dri(session, "iron", "Lactation 31-50 y", "RDA") == 9
    assert _dri(session, "iron", "Infants 7-12 mo", "RDA") == 11
    assert _dri(session, "vitamin_d", "Males 31-50 y", "RDA") == 15
    assert _dri(session, "vitamin_d", "Infants 7-12 mo", "AI") == 10
    assert _dri(session, "calcium", "Children 1-3 y", "RDA") == 700
    assert _dri(session, "protein", "Lactation 19-30 y", "RDA", per_kg=True) == 1.3
    assert _dri(session, "folate_dfe", "Lactation 19-30 y", "RDA") == 500
    assert _dri(session, "vitamin_b12", "Males 19-30 y", "UL") is None  # ND in source

    adult_fat = session.scalar(
        select(Amdr).join(Nutrient).where(Nutrient.key == "fat", Amdr.age_min_months == 228)
    )
    assert (adult_fat.low_pct, adult_fat.high_pct) == (20, 35)

    eq = session.scalar(
        select(EerEquation).where(
            EerEquation.life_stage == "lactation",
            EerEquation.age_min_months == 228,
            EerEquation.postpartum_min_months == 7,
            EerEquation.pa_cat == "active",
        )
    )
    assert eq.constant == pytest.approx(710.25 + 380)


def test_reload_replaces_rather_than_duplicates(session):
    first = load_reference_data(session)
    load_reference_data(session)
    session.commit()
    assert session.query(DriValue).count() == first["dri_values"]
