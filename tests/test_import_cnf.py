import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import func, select

from nutrition.importers.base import (
    ImportValidationError,
    NutrientMapRow,
    load_nutrient_map,
    write_bundle,
)
from nutrition.importers.cnf import CNF_NUTRIENT_MAP, read_cnf
from nutrition.models import Food, FoodNutrient, FoodPortion, Nutrient, SourceNutrient


def _import(session, path):
    report = write_bundle(session, read_cnf(path), load_nutrient_map(CNF_NUTRIENT_MAP))
    session.commit()
    return report


def _amount(session, food_name, key):
    return session.scalar(
        select(FoodNutrient.amount_per_100g)
        .join(Food)
        .join(Nutrient)
        .where(Food.name == food_name, Nutrient.key == key)
    )


def test_imports_foods_groups_and_decodes_latin1(session, cnf_sample):
    report = _import(session, cnf_sample)
    assert report.foods_total == 3 and report.foods_new == 3 and report.groups == 3

    chicken = session.scalar(select(Food).where(Food.source_food_id == "1002"))
    assert chicken.name == "Chicken, broiler, breast, roasted"
    assert chicken.name_fr == "Poulet, poitrine, rôti"
    assert chicken.food_group.name == "Poultry Products"
    b12 = session.scalar(select(SourceNutrient).where(SourceNutrient.source_nutrient_id == "418"))
    assert b12.unit == "µg"


def test_nutrient_values_are_per_100g_and_mapped(session, cnf_sample):
    _import(session, cnf_sample)
    assert _amount(session, "Lentils, red, boiled", "energy_kcal") == 100
    assert _amount(session, "Lentils, red, boiled", "iron") == 3
    assert _amount(session, "Chicken, broiler, breast, roasted", "omega3_dha") == 0.02


def test_vitamin_d_prefers_micrograms_and_falls_back_to_iu(session, cnf_sample):
    _import(session, cnf_sample)
    # Chicken has both fields: the µg value wins over 999 IU.
    assert _amount(session, "Chicken, broiler, breast, roasted", "vitamin_d") == 0.5
    # Lentils only have IU: 40 IU / 40 = 1 µg.
    assert _amount(session, "Lentils, red, boiled", "vitamin_d") == pytest.approx(1.0)


def test_blank_values_and_unmapped_nutrients_are_not_stored(session, cnf_sample):
    _import(session, cnf_sample)
    assert _amount(session, "Beef, ground, pan-fried", "zinc") is None
    moisture = session.scalar(
        select(SourceNutrient).where(SourceNutrient.source_nutrient_id == "255")
    )
    assert moisture is not None and moisture.nutrient_id is None


def test_household_portions_are_converted_to_grams(session, cnf_sample):
    report = _import(session, cnf_sample)
    assert report.portions == 6
    lentil_portions = {
        p.description: p.grams
        for p in session.scalars(
            select(FoodPortion).join(Food).where(Food.source_food_id == "1001")
        )
    }
    assert lentil_portions == {"1 cup": 198.0, "125 mL": 105.0, "100 g": 100.0}


def test_reimport_is_idempotent_and_keeps_ids_stable(session, cnf_sample):
    _import(session, cnf_sample)
    food_ids = dict(session.execute(select(Food.source_food_id, Food.id)).all())
    portion_ids = set(session.scalars(select(FoodPortion.id)))
    n_values = session.scalar(select(func.count()).select_from(FoodNutrient))

    report = _import(session, cnf_sample)
    assert report.foods_new == 0 and report.foods_updated == 3
    assert dict(session.execute(select(Food.source_food_id, Food.id)).all()) == food_ids
    assert set(session.scalars(select(FoodPortion.id))) == portion_ids
    assert session.scalar(select(func.count()).select_from(FoodNutrient)) == n_values


def test_food_dropped_from_new_release_is_deactivated_not_deleted(session, cnf_sample, tmp_path):
    _import(session, cnf_sample)
    newer = tmp_path / "newer"
    shutil.copytree(cnf_sample, newer)
    food_csv = newer / "FOOD NAME.csv"
    lines = food_csv.read_bytes().splitlines(keepends=True)
    food_csv.write_bytes(b"".join(line for line in lines if not line.startswith(b"1003,")))

    report = _import(session, newer)
    assert report.foods_deactivated == 1
    beef = session.scalar(select(Food).where(Food.source_food_id == "1003"))
    assert beef is not None and beef.is_active is False


def test_map_mismatch_aborts_without_writing(session, cnf_sample):
    bad_map = load_nutrient_map(CNF_NUTRIENT_MAP) + [
        NutrientMapRow("303", "calcium", "mg", ["calcium"], 1.0, 0, True, False)
    ]
    with pytest.raises(ImportValidationError, match="map row 303 \\(calcium\\)"):
        write_bundle(session, read_cnf(cnf_sample), bad_map)
    session.rollback()
    assert session.scalar(select(func.count()).select_from(Food)) == 0


def test_missing_required_nutrient_aborts(session, cnf_sample):
    nutrient_map = load_nutrient_map(CNF_NUTRIENT_MAP) + [
        NutrientMapRow("9999", "sodium", "mg", ["sodium"], 1.0, 5, True, False)
    ]
    with pytest.raises(ImportValidationError, match="9999.*not found"):
        write_bundle(session, read_cnf(cnf_sample), nutrient_map)


def test_reads_zip_archive(session, cnf_sample, tmp_path):
    archive = tmp_path / "cnf.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for p in cnf_sample.glob("*.csv"):
            zf.write(p, f"cnf-fcen-csv/{p.name}")
    assert _import(session, archive).foods_total == 3


def test_missing_files_are_reported(tmp_path):
    (tmp_path / "FOOD NAME.csv").write_text("FoodID\n")
    with pytest.raises(FileNotFoundError, match="NUTRIENT AMOUNT"):
        read_cnf(tmp_path)


def test_cli_init_db_and_import(db_url, cnf_sample):
    env = {**os.environ, "NUTRITION_DATABASE_URL": db_url}
    run = [sys.executable, "-m", "nutrition.cli"]
    cwd = Path(__file__).parent.parent
    subprocess.run([*run, "init-db"], env=env, cwd=cwd, check=True, capture_output=True)
    out = subprocess.run(
        [*run, "import-cnf", str(cnf_sample)],
        env=env,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "foods: 3 total (3 new" in out
    assert "vitamin_d" in out
    out = subprocess.run(
        [*run, "seed"], env=env, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout
    assert "Loaded reference data" in out
