"""Source-agnostic import pipeline.

A source-specific reader (CNF today, USDA FoodData Central later) turns its files into an
``ImportBundle``; ``write_bundle`` upserts that bundle into the database. Food ids stay stable
across re-imports so existing log entries keep pointing at the right foods.
"""

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from nutrition.models import (
    DataSource,
    Food,
    FoodGroup,
    FoodNutrient,
    FoodPortion,
    Nutrient,
    SourceNutrient,
)

SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds"


class ImportValidationError(Exception):
    """Raised when source data doesn't match what the nutrient map expects."""


@dataclass
class SourceInfo:
    code: str
    name: str
    version: str | None = None
    url: str | None = None


@dataclass
class NutrientDef:
    source_nutrient_id: str
    name: str
    unit: str
    symbol: str | None = None


@dataclass
class GroupDef:
    source_group_id: str
    name: str
    name_fr: str | None = None


@dataclass
class FoodDef:
    source_food_id: str
    name: str
    source_group_id: str | None = None
    name_fr: str | None = None


@dataclass
class PortionDef:
    source_food_id: str
    source_measure_id: str
    description: str
    grams: float


@dataclass
class ImportBundle:
    source: SourceInfo
    nutrients: list[NutrientDef]
    groups: list[GroupDef]
    foods: list[FoodDef]
    # (source_food_id, source_nutrient_id, amount per 100 g)
    amounts: list[tuple[str, str, float]]
    portions: list[PortionDef]


@dataclass
class NutrientMapRow:
    source_nutrient_id: str
    nutrient_key: str
    expected_unit: str
    name_must_contain: list[str]
    factor: float
    priority: int
    required: bool
    verified: bool


@dataclass
class ImportReport:
    source_code: str
    foods_total: int = 0
    foods_new: int = 0
    foods_updated: int = 0
    foods_deactivated: int = 0
    groups: int = 0
    portions: int = 0
    nutrient_rows: int = 0
    mapped_nutrients: list[tuple[str, str, str, str]] = field(default_factory=list)
    coverage: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def format(self) -> str:
        lines = [
            f"Import of '{self.source_code}' complete",
            f"  foods: {self.foods_total} total ({self.foods_new} new, "
            f"{self.foods_updated} updated, {self.foods_deactivated} deactivated)",
            f"  food groups: {self.groups}",
            f"  household portions: {self.portions}",
            f"  nutrient values stored: {self.nutrient_rows}",
            "  nutrient mapping (canonical <- source id: source name [unit]):",
        ]
        for key, sid, name, unit in self.mapped_nutrients:
            lines.append(f"    {key:<14} <- {sid}: {name} [{unit}]")
        lines.append("  coverage (foods with a value / active foods):")
        for key, n in self.coverage.items():
            pct = 100 * n / self.foods_total if self.foods_total else 0
            lines.append(f"    {key:<14} {n:>6} ({pct:5.1f}%)")
        for w in self.warnings:
            lines.append(f"  WARNING: {w}")
        return "\n".join(lines)


def normalize_unit(unit: str) -> str:
    u = unit.strip().replace("μ", "µ")  # Greek mu -> micro sign
    lowered = u.lower()
    if lowered in {"ug", "mcg", "µg"}:
        return "µg"
    if lowered in {"kcal"}:
        return "kcal"
    if lowered in {"iu"}:
        return "IU"
    if lowered in {"g", "mg", "kj"}:
        return lowered
    return u


def load_nutrient_map(path: Path) -> list[NutrientMapRow]:
    with path.open(encoding="utf-8", newline="") as f:
        return [
            NutrientMapRow(
                source_nutrient_id=row["source_nutrient_id"].strip(),
                nutrient_key=row["nutrient_key"].strip(),
                expected_unit=normalize_unit(row["expected_unit"]),
                name_must_contain=[t.strip().lower() for t in row["name_must_contain"].split("|")],
                factor=float(row["factor"]),
                priority=int(row["priority"]),
                required=row["required"].strip().lower() == "yes",
                verified=row["verified"].strip().lower() == "yes",
            )
            for row in csv.DictReader(f)
        ]


def seed_nutrients(session: Session, path: Path | None = None) -> None:
    """Insert or update the canonical nutrient list."""
    path = path or SEEDS_DIR / "nutrients.csv"
    existing = {n.key: n for n in session.scalars(select(Nutrient))}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            n = existing.get(row["key"]) or Nutrient(key=row["key"])
            n.name = row["name"]
            n.unit = row["unit"]
            n.display_order = int(row["display_order"])
            n.is_key = row["is_key"] == "1"
            session.add(n)
    session.flush()


@dataclass
class MapProblem:
    message: str
    fatal: bool


def _validate_map(
    bundle: ImportBundle, nutrient_map: list[NutrientMapRow], nutrients_by_key: dict[str, Nutrient]
) -> list[MapProblem]:
    defs = {d.source_nutrient_id: d for d in bundle.nutrients}
    problems = []
    for m in nutrient_map:
        label = f"map row {m.source_nutrient_id} ({m.nutrient_key})"
        if m.nutrient_key not in nutrients_by_key:
            problems.append(MapProblem(f"{label}: unknown nutrient key", True))
            continue
        d = defs.get(m.source_nutrient_id)
        if d is None:
            # An optional row (e.g. a fallback field) may be absent from a given release.
            problems.append(MapProblem(f"{label}: not found in source nutrients", m.required))
            continue
        if normalize_unit(d.unit) != m.expected_unit:
            problems.append(
                MapProblem(f"{label}: unit {d.unit!r}, expected {m.expected_unit!r}", True)
            )
        missing = [t for t in m.name_must_contain if t not in d.name.lower()]
        if missing:
            problems.append(MapProblem(f"{label}: source name {d.name!r} lacks {missing}", True))
    # Optional rows are fallbacks: each mapped nutrient still needs at least one source field.
    for key in {m.nutrient_key for m in nutrient_map}:
        if not any(m.source_nutrient_id in defs for m in nutrient_map if m.nutrient_key == key):
            problems.append(MapProblem(f"nutrient {key!r}: no mapped source field present", True))
    return problems


def write_bundle(
    session: Session,
    bundle: ImportBundle,
    nutrient_map: list[NutrientMapRow],
) -> ImportReport:
    """Upsert a bundle. Validates the nutrient map first and writes nothing if it fails."""
    seed_nutrients(session)
    nutrients_by_key = {n.key: n for n in session.scalars(select(Nutrient))}
    report = ImportReport(source_code=bundle.source.code)

    hard_errors = []
    for problem in _validate_map(bundle, nutrient_map, nutrients_by_key):
        if problem.fatal:
            hard_errors.append(problem.message)
        else:
            report.warnings.append(problem.message)
    if hard_errors:
        raise ImportValidationError(
            "Nutrient map does not match the source data:\n  " + "\n  ".join(hard_errors)
        )

    # --- source -------------------------------------------------------------------------
    source = session.scalar(select(DataSource).where(DataSource.code == bundle.source.code))
    if source is None:
        source = DataSource(code=bundle.source.code)
    source.name = bundle.source.name
    source.version = bundle.source.version
    source.url = bundle.source.url
    source.imported_at = datetime.now()
    session.add(source)
    session.flush()

    # --- source nutrients + mapping -----------------------------------------------------
    map_by_sid = {m.source_nutrient_id: m for m in nutrient_map}
    existing_sn = {
        sn.source_nutrient_id: sn
        for sn in session.scalars(
            select(SourceNutrient).where(SourceNutrient.source_id == source.id)
        )
    }
    sn_by_sid: dict[str, SourceNutrient] = {}
    for d in bundle.nutrients:
        sn = existing_sn.get(d.source_nutrient_id) or SourceNutrient(
            source_id=source.id, source_nutrient_id=d.source_nutrient_id
        )
        sn.name, sn.unit, sn.symbol = d.name, d.unit, d.symbol
        m = map_by_sid.get(d.source_nutrient_id)
        if m:
            sn.nutrient_id = nutrients_by_key[m.nutrient_key].id
            sn.factor, sn.priority = m.factor, m.priority
            report.mapped_nutrients.append((m.nutrient_key, d.source_nutrient_id, d.name, d.unit))
        else:
            sn.nutrient_id, sn.factor, sn.priority = None, 1.0, 0
        session.add(sn)
        sn_by_sid[d.source_nutrient_id] = sn
    session.flush()
    report.mapped_nutrients.sort()

    # --- groups ------------------------------------------------------------------------
    existing_groups = {
        g.source_group_id: g
        for g in session.scalars(select(FoodGroup).where(FoodGroup.source_id == source.id))
    }
    group_by_sid: dict[str, FoodGroup] = {}
    for gd in bundle.groups:
        g = existing_groups.get(gd.source_group_id) or FoodGroup(
            source_id=source.id, source_group_id=gd.source_group_id
        )
        g.name, g.name_fr = gd.name, gd.name_fr
        session.add(g)
        group_by_sid[gd.source_group_id] = g
    session.flush()
    report.groups = len(group_by_sid)

    # --- foods -------------------------------------------------------------------------
    existing_foods = {
        f.source_food_id: f
        for f in session.scalars(select(Food).where(Food.source_id == source.id))
    }
    seen: set[str] = set()
    for fd in bundle.foods:
        f = existing_foods.get(fd.source_food_id)
        if f is None:
            f = Food(source_id=source.id, source_food_id=fd.source_food_id)
            report.foods_new += 1
        else:
            report.foods_updated += 1
        f.name, f.name_fr, f.is_active = fd.name, fd.name_fr, True
        group = group_by_sid.get(fd.source_group_id) if fd.source_group_id else None
        f.food_group_id = group.id if group else None
        session.add(f)
        existing_foods[fd.source_food_id] = f
        seen.add(fd.source_food_id)
    for sid, f in existing_foods.items():
        if sid not in seen and f.is_active:
            f.is_active = False
            report.foods_deactivated += 1
    session.flush()
    report.foods_total = len(seen)
    food_id_by_sid = {sid: f.id for sid, f in existing_foods.items()}

    # --- nutrient amounts: replace this source's rows wholesale -----------------------------
    source_food_ids = select(Food.id).where(Food.source_id == source.id)
    session.execute(delete(FoodNutrient).where(FoodNutrient.food_id.in_(source_food_ids)))

    # For each (food, canonical nutrient) keep the value from the highest-priority mapping.
    best: dict[tuple[int, int], tuple[int, float, int]] = {}
    for food_sid, nut_sid, amount in bundle.amounts:
        sn = sn_by_sid.get(nut_sid)
        food_id = food_id_by_sid.get(food_sid)
        if sn is None or sn.nutrient_id is None or food_id is None:
            continue
        k = (food_id, sn.nutrient_id)
        cand = (sn.priority, amount * sn.factor, sn.id)
        if k not in best or cand[0] < best[k][0]:
            best[k] = cand
    rows = [
        {"food_id": fid, "nutrient_id": nid, "amount_per_100g": v, "source_nutrient_id": snid}
        for (fid, nid), (_p, v, snid) in best.items()
    ]
    if rows:
        session.execute(insert(FoodNutrient), rows)
    report.nutrient_rows = len(rows)

    coverage: dict[int, int] = defaultdict(int)
    for _fid, nid in best:
        coverage[nid] += 1
    for key, n in sorted(nutrients_by_key.items(), key=lambda kv: kv[1].display_order):
        report.coverage[key] = coverage.get(n.id, 0)

    # --- portions: upsert by (food, measure) so ids referenced by logs stay stable ---------
    existing_portions = {
        (p.food_id, p.source_measure_id): p
        for p in session.scalars(
            select(FoodPortion).where(
                FoodPortion.origin == "source", FoodPortion.food_id.in_(source_food_ids)
            )
        )
    }
    for pd in bundle.portions:
        food_id = food_id_by_sid.get(pd.source_food_id)
        if food_id is None:
            continue
        p = existing_portions.pop((food_id, pd.source_measure_id), None) or FoodPortion(
            food_id=food_id, origin="source", source_measure_id=pd.source_measure_id
        )
        p.description, p.grams = pd.description, pd.grams
        session.add(p)
        report.portions += 1
    session.flush()

    unverified = [m.source_nutrient_id for m in nutrient_map if not m.verified]
    if unverified:
        report.warnings.append(
            f"{len(unverified)} nutrient map rows are not yet marked verified; "
            "check the mapping table above against the source documentation"
        )
    return report
