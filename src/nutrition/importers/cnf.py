"""Reader for Health Canada's Canadian Nutrient File (CNF) CSV release.

Accepts either the downloaded .zip or a directory of the extracted CSV files. Uses:

  FOOD NAME.csv, FOOD GROUP.csv, NUTRIENT NAME.csv, NUTRIENT AMOUNT.csv,
  MEASURE NAME.csv, CONVERSION FACTOR.csv

CNF nutrient amounts are per 100 g edible portion. A household measure's conversion factor
multiplies the per-100 g amounts, so the measure's weight is ``factor * 100`` grams.
"""

import csv
import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

from nutrition.importers.base import (
    SEEDS_DIR,
    FoodDef,
    GroupDef,
    ImportBundle,
    NutrientDef,
    PortionDef,
    SourceInfo,
)

CNF_URL = (
    "https://www.canada.ca/en/health-canada/services/food-nutrition/healthy-eating/"
    "nutrient-data/canadian-nutrient-file-2015-download-files.html"
)
CNF_NUTRIENT_MAP = SEEDS_DIR / "cnf_nutrient_map.csv"

REQUIRED_FILES = [
    "FOOD NAME",
    "FOOD GROUP",
    "NUTRIENT NAME",
    "NUTRIENT AMOUNT",
    "MEASURE NAME",
    "CONVERSION FACTOR",
]


def _decode(raw: bytes) -> str:
    # CNF files are published as Latin-1; accept UTF-8 too in case a copy was re-saved.
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


class _CnfFiles:
    """Case-insensitive access to the CNF CSVs inside a directory or zip."""

    def __init__(self, path: Path):
        self.path = path
        self._raw: dict[str, bytes] = {}
        if path.is_dir():
            for p in path.rglob("*.csv"):
                self._raw[p.stem.strip().upper()] = p.read_bytes()
        elif zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    if name.lower().endswith(".csv"):
                        self._raw[Path(name).stem.strip().upper()] = zf.read(name)
        else:
            raise FileNotFoundError(f"{path} is neither a directory nor a zip file")
        missing = [f for f in REQUIRED_FILES if f not in self._raw]
        if missing:
            raise FileNotFoundError(f"CNF files missing from {path}: {', '.join(missing)}")

    def rows(self, name: str) -> Iterator[dict[str, str]]:
        reader = csv.DictReader(io.StringIO(_decode(self._raw[name]), newline=""))
        for row in reader:
            # Normalise header case/whitespace so minor release differences don't matter.
            yield {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}


def _float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def read_cnf(path: Path, version: str = "2015") -> ImportBundle:
    files = _CnfFiles(path)

    groups = [
        GroupDef(r["foodgroupid"], r["foodgroupname"], r.get("foodgroupnamef") or None)
        for r in files.rows("FOOD GROUP")
    ]
    foods = [
        FoodDef(
            source_food_id=r["foodid"],
            name=r["fooddescription"],
            source_group_id=r.get("foodgroupid") or None,
            name_fr=r.get("fooddescriptionf") or None,
        )
        for r in files.rows("FOOD NAME")
    ]
    nutrients = [
        NutrientDef(
            source_nutrient_id=r["nutrientid"],
            name=r["nutrientname"],
            unit=r["nutrientunit"],
            symbol=r.get("nutrientsymbol") or None,
        )
        for r in files.rows("NUTRIENT NAME")
    ]
    amounts = []
    for r in files.rows("NUTRIENT AMOUNT"):
        value = _float(r["nutrientvalue"])
        if value is not None:
            amounts.append((r["foodid"], r["nutrientid"], value))

    measures = {r["measureid"]: r["measuredescription"] for r in files.rows("MEASURE NAME")}
    portions = []
    for r in files.rows("CONVERSION FACTOR"):
        factor = _float(r["conversionfactorvalue"])
        desc = measures.get(r["measureid"])
        if factor is None or factor <= 0 or not desc:
            continue
        portions.append(
            PortionDef(
                source_food_id=r["foodid"],
                source_measure_id=r["measureid"],
                description=desc,
                grams=round(factor * 100, 2),
            )
        )

    return ImportBundle(
        source=SourceInfo(
            code="cnf", name="Canadian Nutrient File (Health Canada)", version=version, url=CNF_URL
        ),
        nutrients=nutrients,
        groups=groups,
        foods=foods,
        amounts=amounts,
        portions=portions,
    )
