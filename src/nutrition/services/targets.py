"""Personal daily targets: Health Canada DRIs, adjusted for goal, diet and manual overrides.

Every target carries notes and sources so the UI can show where a number came from.
"""

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from nutrition.models import Amdr, DriValue, EerEquation, Nutrient, Person

# Muscle-gain goal (agreed with the household; see README "Targets").
GAIN_PROTEIN_G_PER_KG = 1.6
GAIN_PROTEIN_UPPER_G_PER_KG = 2.2
IRAKI_2019 = "https://pmc.ncbi.nlm.nih.gov/articles/PMC6680710/"
GAIN_PROTEIN_SOURCES = [
    ("Morton et al. 2018, Br J Sports Med: no further muscle gain above ~1.62 g/kg/day",
     "https://bjsm.bmj.com/content/52/6/376"),
    ("Iraki et al. 2019, Sports: 1.6-2.2 g/kg/day", IRAKI_2019),
]  # fmt: skip
GAIN_ENERGY_SOURCE = (
    "Iraki et al. 2019, Sports: 10-20% surplus, aiming for 0.25-0.5% body weight gain per week",
    IRAKI_2019,
)
EER_URL = (
    "https://www.canada.ca/en/health-canada/services/food-nutrition/healthy-eating/"
    "dietary-reference-intakes/tables/equations-estimate-energy-requirement.html"
)
VEGETARIAN_IRON_FACTOR = 1.8

# Nutrients with a ceiling but no meaningful "goal" to reach.
LIMIT_ONLY = {"sodium"}
# The folate UL applies only to synthetic folic acid, which CNF reports separately.
UPPER_COMPARED_TO = {"folate_dfe": "folic_acid"}


@dataclass
class Target:
    key: str
    name: str
    unit: str
    amount: float | None = None  # daily goal
    kind: str | None = None  # RDA | AI | EER | goal | override
    upper: float | None = None  # intake above this is flagged
    upper_kind: str | None = None  # UL | CDRR
    upper_nutrient_key: str | None = None  # compare the upper limit against this nutrient
    amdr_pct: tuple[float, float] | None = None  # acceptable range, % of energy
    notes: list[str] = field(default_factory=list)
    sources: list[tuple[str, str]] = field(default_factory=list)  # (label, url)
    missing_reason: str | None = None


@dataclass
class PersonTargets:
    person: Person
    on_date: date
    age_months: int
    group_label: str | None
    infant_mode: bool
    targets: dict[str, Target]


def age_in_months(dob: date, on: date) -> int:
    """Completed months of age on a given date."""
    months = (on.year - dob.year) * 12 + (on.month - dob.month)
    if on.day < dob.day:
        months -= 1
    return max(months, 0)


def infant_mode(person: Person, on: date) -> bool:
    if person.milk_feeds_not_logged is not None:
        return person.milk_feeds_not_logged
    return age_in_months(person.date_of_birth, on) < 12


def effective_life_stage(person: Person, age_months: int) -> str:
    # Pregnancy/lactation DRI groups exist for females 14 and older only.
    if person.sex == "F" and age_months >= 168 and person.life_stage in ("pregnancy", "lactation"):
        return person.life_stage
    return "general"


def _dri_rows(session: Session, sex: str, stage: str, age: int) -> list[DriValue]:
    return list(
        session.scalars(
            select(DriValue).where(
                DriValue.sex.in_([sex, "any"]),
                DriValue.life_stage == stage,
                DriValue.age_min_months <= age,
                or_(DriValue.age_max_months.is_(None), DriValue.age_max_months > age),
            )
        )
    )


def estimated_energy_requirement(
    session: Session, person: Person, on: date
) -> tuple[float | None, str | None, str | None]:
    """Returns (kcal/day, equation text, reason if it can't be computed)."""
    if person.height_cm is None or person.weight_kg is None:
        return None, None, "Add height and weight to estimate calorie needs."
    age = age_in_months(person.date_of_birth, on)
    stage = effective_life_stage(person, age)
    if stage == "pregnancy":
        return (
            None,
            None,
            (
                "Pregnancy energy equations need gestation week and pre-pregnancy BMI, which the "
                "app doesn't collect; set a manual calorie target instead."
            ),
        )
    postpartum = None
    if stage == "lactation":
        if person.postpartum_since is None:
            return None, None, "Add the baby's birth date to estimate breastfeeding calorie needs."
        postpartum = age_in_months(person.postpartum_since, on)
        if postpartum > 12:
            stage = "general"  # equations are published for 0-12 months postpartum only
    q = select(EerEquation).where(
        EerEquation.life_stage == stage,
        EerEquation.sex.in_([person.sex, "any"]),
        EerEquation.age_min_months <= age,
        or_(EerEquation.age_max_months.is_(None), EerEquation.age_max_months > age),
        EerEquation.pa_cat.in_([person.activity_level, "any"]),
    )
    if stage == "lactation":
        q = q.where(
            EerEquation.postpartum_min_months <= postpartum,
            EerEquation.postpartum_max_months >= postpartum,
        )
    eq = session.scalars(q).first()
    if eq is None:
        return None, None, "No published energy equation matches this profile."
    age_years = (on - person.date_of_birth).days / 365.25
    kcal = (
        eq.constant
        + eq.age_years_coef * age_years
        + eq.height_cm_coef * person.height_cm
        + eq.weight_kg_coef * person.weight_kg
    )
    return kcal, eq.equation_text, None


def compute_targets(session: Session, person: Person, on: date | None = None) -> PersonTargets:
    on = on or date.today()
    age = age_in_months(person.date_of_birth, on)
    stage = effective_life_stage(person, age)
    nutrients = {n.key: n for n in session.scalars(select(Nutrient))}
    targets = {
        n.key: Target(key=n.key, name=n.name, unit=n.unit)
        for n in sorted(nutrients.values(), key=lambda n: n.display_order)
    }

    rows = _dri_rows(session, person.sex, stage, age)
    by_nutrient: dict[int, list[DriValue]] = {}
    for r in rows:
        by_nutrient.setdefault(r.nutrient_id, []).append(r)
    group_label = rows[0].age_label if rows else None

    for key, t in targets.items():
        values = by_nutrient.get(nutrients[key].id, [])
        if not values:
            continue
        _apply_dri(t, values, person.weight_kg)
        if key in LIMIT_ONLY:
            t.amount, t.kind = None, None
            if t.upper is None:
                t.missing_reason = "Health Canada sets no upper limit for this age group."
        elif t.amount is None:
            t.missing_reason = "Health Canada: not determinable for this age group."
        if key in UPPER_COMPARED_TO and t.upper is not None:
            t.upper_nutrient_key = UPPER_COMPARED_TO[key]

    for a in session.scalars(
        select(Amdr).where(
            Amdr.age_min_months <= age,
            or_(Amdr.age_max_months.is_(None), Amdr.age_max_months > age),
        )
    ):
        t = targets[a.nutrient.key]
        t.amdr_pct = (a.low_pct, a.high_pct)
        t.sources.append(("Health Canada: Acceptable Macronutrient Distribution Ranges",
                          a.source_url))  # fmt: skip

    energy = targets["energy_kcal"]
    kcal, equation, reason = estimated_energy_requirement(session, person, on)
    if kcal is None:
        energy.missing_reason = reason
    else:
        energy.amount, energy.kind = round(kcal), "EER"
        energy.notes.append(f"Estimated Energy Requirement: {equation}")
        energy.sources.append(("Health Canada: equations to estimate energy requirement", EER_URL))

    if person.goal == "gain_muscle":
        _apply_gain_goal(targets, person)
    if person.vegetarian_iron:
        iron = targets["iron"]
        if iron.amount is not None:
            iron.amount = round(iron.amount * VEGETARIAN_IRON_FACTOR, 1)
            iron.notes.append(
                f"Multiplied by {VEGETARIAN_IRON_FACTOR} for a vegetarian diet (Health Canada "
                "footnote: iron from vegetarian diets is less bioavailable)."
            )
    for o in person.overrides:
        t = targets[o.nutrient.key]
        t.amount, t.kind, t.missing_reason = o.amount, "override", None
        t.notes.append(f"Manual target{': ' + o.note if o.note else ''}")

    for t in targets.values():
        if t.amount is None and t.missing_reason is None and not t.upper and not t.amdr_pct:
            t.missing_reason = "No Health Canada reference value."

    return PersonTargets(person, on, age, group_label, infant_mode(person, on), targets)


def _apply_dri(t: Target, values: list[DriValue], weight_kg: float | None) -> None:
    def pick(vtype: str, per_kg: bool = False) -> DriValue | None:
        return next(
            (v for v in values
             if v.value_type == vtype and v.per_kg_body_weight == per_kg and v.status == "ok"),
            None,
        )  # fmt: skip

    goal = None
    per_kg_goal = pick("RDA", per_kg=True) or pick("AI", per_kg=True)
    if per_kg_goal and weight_kg:
        goal = per_kg_goal
        t.amount = round(per_kg_goal.amount * weight_kg, 1)
        t.notes.append(f"{per_kg_goal.value_type} {per_kg_goal.amount:g} g/kg x {weight_kg:g} kg")
    else:
        goal = pick("RDA") or pick("AI")
        if goal:
            t.amount = goal.amount
    if goal:
        t.kind = goal.value_type
        t.sources.append((f"Health Canada DRI tables, {goal.age_label}", goal.source_url))
        if goal.footnotes:
            t.notes.extend(goal.footnotes.split(" | "))
    upper = pick("UL") or pick("CDRR")
    if upper:
        t.upper, t.upper_kind = upper.amount, upper.value_type
        if upper.footnotes and upper is not goal:
            t.notes.extend(n for n in upper.footnotes.split(" | ") if n not in t.notes)


def _apply_gain_goal(targets: dict[str, Target], person: Person) -> None:
    energy = targets["energy_kcal"]
    if energy.amount is not None:
        pct = person.energy_surplus_pct
        energy.amount = round(energy.amount * (1 + pct / 100))
        energy.kind = "goal"
        energy.notes.append(f"+{pct:g}% surplus for muscle gain; check against weight trend.")
        energy.sources.append(GAIN_ENERGY_SOURCE)
    protein = targets["protein"]
    if person.weight_kg:
        goal = round(GAIN_PROTEIN_G_PER_KG * person.weight_kg, 1)
        if protein.amount is None or goal > protein.amount:
            protein.amount, protein.kind = goal, "goal"
            protein.notes.append(
                f"Muscle-gain goal {GAIN_PROTEIN_G_PER_KG:g} g/kg x {person.weight_kg:g} kg "
                f"(evidence range up to {GAIN_PROTEIN_UPPER_G_PER_KG:g} g/kg)."
            )
            protein.sources.extend(GAIN_PROTEIN_SOURCES)
    else:
        protein.notes.append("Add weight to apply the muscle-gain protein goal.")
