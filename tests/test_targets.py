from datetime import date

import pytest
from sqlalchemy import select

from nutrition.models import Nutrient, Person, TargetOverride
from nutrition.seeds.load import load_reference_data
from nutrition.services.targets import age_in_months, compute_targets

TODAY = date(2026, 10, 19)
BABY_DOB = date(2026, 2, 15)


@pytest.fixture
def db(session):
    load_reference_data(session)
    return session


def _person(db, **kw) -> Person:
    defaults = dict(name="X", date_of_birth=date(1990, 6, 15), sex="M", activity_level="active")
    p = Person(**{**defaults, **kw})
    db.add(p)
    db.flush()
    return p


def _years(dob: date, on: date = TODAY) -> float:
    return (on - dob).days / 365.25


@pytest.mark.parametrize(
    "dob,on,expected",
    [
        (date(2026, 2, 15), date(2026, 10, 19), 8),
        (date(2026, 2, 15), date(2027, 2, 14), 11),
        (date(2026, 2, 15), date(2027, 2, 15), 12),
        (date(2026, 1, 31), date(2026, 2, 28), 0),
    ],
)
def test_age_in_completed_months(dob, on, expected):
    assert age_in_months(dob, on) == expected


def test_adult_male_maintain(db):
    p = _person(db, height_cm=178, weight_kg=70)
    t = compute_targets(db, p, TODAY)
    assert t.group_label == "Males 31-50 y"
    assert not t.infant_mode
    # Published equation, adult male, PA CAT active.
    eer = 1004.82 - 10.83 * _years(p.date_of_birth) + 6.52 * 178 + 15.91 * 70
    assert t.targets["energy_kcal"].amount == round(eer)
    assert t.targets["protein"].amount == pytest.approx(0.8 * 70)
    assert t.targets["iron"].amount == 8 and t.targets["iron"].kind == "RDA"
    assert t.targets["vitamin_d"].amount == 15
    assert t.targets["fibre"].kind == "AI"
    assert t.targets["fat"].amdr_pct == (20, 35)


def test_muscle_gain_goal(db):
    p = _person(db, height_cm=178, weight_kg=70, goal="gain_muscle", energy_surplus_pct=15)
    t = compute_targets(db, p, TODAY).targets
    eer = 1004.82 - 10.83 * _years(p.date_of_birth) + 6.52 * 178 + 15.91 * 70
    assert t["energy_kcal"].amount == round(round(eer) * 1.15)
    assert t["energy_kcal"].kind == "goal"
    assert t["protein"].amount == pytest.approx(112.0)
    assert any("Morton" in label for label, _url in t["protein"].sources)


@pytest.mark.parametrize(
    "baby_dob,expected_extra",
    [(date(2026, 4, 19), 540 - 140), (date(2026, 3, 19), 380), (date(2026, 2, 15), 380)],
)
def test_breastfeeding_uses_postpartum_equation(db, baby_dob, expected_extra):
    # Baby aged 6 months -> "0 to 6 months" equation; 7 or 8 months -> "7 to 12 months".
    mum = _person(
        db, sex="F", date_of_birth=date(1992, 3, 1), height_cm=162, weight_kg=60,
        life_stage="lactation", postpartum_since=baby_dob,
    )  # fmt: skip
    t = compute_targets(db, mum, TODAY)
    assert t.group_label == "Lactation 31-50 y"
    base = 710.25 - 7.01 * _years(mum.date_of_birth) + 6.54 * 162 + 12.34 * 60
    assert t.targets["energy_kcal"].amount == round(base + expected_extra)
    assert t.targets["iron"].amount == 9
    assert t.targets["protein"].amount == pytest.approx(1.3 * 60)
    assert t.targets["folate_dfe"].amount == 500


def test_breastfeeding_without_baby_birth_date_explains_missing_energy(db):
    mum = _person(db, sex="F", height_cm=162, weight_kg=60, life_stage="lactation")
    energy = compute_targets(db, mum, TODAY).targets["energy_kcal"]
    assert energy.amount is None and "birth date" in energy.missing_reason


def test_pregnancy_energy_is_not_guessed(db):
    p = _person(db, sex="F", height_cm=162, weight_kg=60, life_stage="pregnancy")
    t = compute_targets(db, p, TODAY)
    assert t.group_label == "Pregnancy 31-50 y"
    assert t.targets["energy_kcal"].amount is None
    assert t.targets["iron"].amount == 27


def test_missing_height_explains_missing_energy(db):
    energy = compute_targets(db, _person(db, weight_kg=70), TODAY).targets["energy_kcal"]
    assert energy.amount is None and "height" in energy.missing_reason


def test_infant_targets_follow_age(db):
    baby = _person(db, date_of_birth=BABY_DOB, height_cm=70, weight_kg=8.5)
    at_8m = compute_targets(db, baby, TODAY)
    assert at_8m.group_label == "Infants 7-12 mo" and at_8m.infant_mode
    assert at_8m.targets["iron"].amount == 11
    assert at_8m.targets["vitamin_d"].kind == "AI"
    eer = -716.45 - 1.00 * _years(BABY_DOB) + 17.82 * 70 + 15.06 * 8.5 + 20
    assert at_8m.targets["energy_kcal"].amount == round(eer)

    day_before_birthday = compute_targets(db, baby, date(2027, 2, 14))
    assert day_before_birthday.group_label == "Infants 7-12 mo" and day_before_birthday.infant_mode

    at_1y = compute_targets(db, baby, date(2027, 2, 15))
    assert at_1y.group_label == "Children 1-3 y" and not at_1y.infant_mode
    assert at_1y.targets["iron"].amount == 7
    assert at_1y.targets["carbohydrate"].amdr_pct == (45, 65)

    baby.milk_feeds_not_logged = True  # parents keep milk mode on past the first birthday
    assert compute_targets(db, baby, date(2027, 2, 15)).infant_mode


def test_vegetarian_iron_multiplier(db):
    p = _person(db, sex="F", date_of_birth=date(2000, 1, 1), vegetarian_iron=True)
    iron = compute_targets(db, p, TODAY).targets["iron"]
    assert iron.amount == pytest.approx(18 * 1.8)
    assert any("vegetarian" in n for n in iron.notes)


def test_upper_limits(db):
    t = compute_targets(db, _person(db), TODAY).targets
    assert (t["folate_dfe"].upper, t["folate_dfe"].upper_nutrient_key) == (1000, "folic_acid")
    assert (t["sodium"].amount, t["sodium"].upper, t["sodium"].upper_kind) == (None, 2300, "CDRR")
    assert t["vitamin_b12"].upper is None  # not determinable in source
    assert t["calcium"].upper == 2500


def test_missing_reasons_follow_the_source(db):
    baby = _person(db, date_of_birth=BABY_DOB)
    t = compute_targets(db, baby, TODAY).targets
    assert "not determinable" in t["fibre"].missing_reason  # ND for infants
    assert "no upper limit" in t["sodium"].missing_reason  # AI only, no UL/CDRR


def test_manual_override_wins(db):
    p = _person(db, height_cm=178, weight_kg=70)
    energy = db.scalar(select(Nutrient).where(Nutrient.key == "energy_kcal"))
    p.overrides.append(TargetOverride(nutrient=energy, amount=3200, note="coach plan"))
    db.flush()
    e = compute_targets(db, p, TODAY).targets["energy_kcal"]
    assert (e.amount, e.kind) == (3200, "override")
    assert "coach plan" in e.notes[-1]
