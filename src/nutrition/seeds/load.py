"""Loads reference seed files (nutrients, DRIs, AMDRs, EER equations) into the database."""

import csv
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from nutrition.importers.base import SEEDS_DIR, seed_nutrients
from nutrition.models import Amdr, DriValue, EerEquation, Nutrient


class SeedError(Exception):
    pass


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _opt_int(v: str) -> int | None:
    return int(v) if v != "" else None


def _opt_float(v: str) -> float | None:
    return float(v) if v != "" else None


def load_reference_data(session: Session, seeds_dir: Path = SEEDS_DIR) -> dict[str, int]:
    """Replace DRI, AMDR and EER tables with the seed files' contents."""
    seed_nutrients(session, seeds_dir / "nutrients.csv")
    nutrients = {n.key: n for n in session.scalars(select(Nutrient))}

    def nutrient(key: str, unit: str | None = None) -> Nutrient:
        n = nutrients.get(key)
        if n is None:
            raise SeedError(f"unknown nutrient key {key!r}")
        if unit is not None and unit != n.unit:
            raise SeedError(f"{key}: seed unit {unit!r} differs from canonical unit {n.unit!r}")
        return n

    session.execute(delete(DriValue))
    session.execute(delete(Amdr))
    session.execute(delete(EerEquation))

    dri = _rows(seeds_dir / "dri_values.csv")
    for r in dri:
        session.add(
            DriValue(
                nutrient=nutrient(r["nutrient_key"], r["unit"]),
                sex=r["sex"],
                life_stage=r["life_stage"],
                age_min_months=int(r["age_min_months"]),
                age_max_months=_opt_int(r["age_max_months"]),
                age_label=r["age_label"],
                value_type=r["value_type"],
                amount=_opt_float(r["amount"]),
                unit=r["unit"],
                per_kg_body_weight=r["per_kg_body_weight"] == "yes",
                status=r["status"],
                footnotes=r["footnotes"],
                source_table=r["source_table"],
                source_url=r["source_url"],
                source_date=r["source_date"],
            )
        )

    amdr = _rows(seeds_dir / "amdr.csv")
    for r in amdr:
        session.add(
            Amdr(
                nutrient=nutrient(r["nutrient_key"]),
                age_min_months=int(r["age_min_months"]),
                age_max_months=_opt_int(r["age_max_months"]),
                age_label=r["age_label"],
                low_pct=float(r["low_pct"]),
                high_pct=float(r["high_pct"]),
                source_url=r["source_url"],
                source_date=r["source_date"],
            )
        )

    eer = _rows(seeds_dir / "eer_equations.csv")
    for r in eer:
        session.add(
            EerEquation(
                life_stage=r["life_stage"],
                sex=r["sex"],
                age_min_months=int(r["age_min_months"]),
                age_max_months=_opt_int(r["age_max_months"]),
                postpartum_min_months=_opt_int(r["postpartum_min_months"]),
                postpartum_max_months=_opt_int(r["postpartum_max_months"]),
                pre_pregnancy_bmi=r["pre_pregnancy_bmi"] or None,
                pa_cat=r["pa_cat"],
                constant=float(r["constant"]),
                age_years_coef=float(r["age_years_coef"]),
                height_cm_coef=float(r["height_cm_coef"]),
                weight_kg_coef=float(r["weight_kg_coef"]),
                gestation_weeks_coef=float(r["gestation_weeks_coef"]),
                equation_text=r["equation_text"],
                source_url=r["source_url"],
                source_date=r["source_date"],
            )
        )
    session.flush()
    return {"dri_values": len(dri), "amdr": len(amdr), "eer_equations": len(eer)}
