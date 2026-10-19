from datetime import date
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from nutrition.models import Nutrient, Person, TargetOverride, WeightEntry
from nutrition.models.people import ACTIVITY_LEVELS, GOALS, LIFE_STAGES, SEXES
from nutrition.services.targets import age_in_months, compute_targets
from nutrition.web.deps import CurrentUser, DbSession, get_or_404, templates

router = APIRouter()


def _form_context(person: Person | None, values: dict, errors: dict) -> dict:
    return {
        "person": person,
        "v": values,
        "errors": errors,
        "sexes": SEXES,
        "activity_levels": ACTIVITY_LEVELS,
        "life_stages": LIFE_STAGES,
        "goals": GOALS,
    }


def _values_from(person: Person) -> dict:
    return {
        "name": person.name,
        "date_of_birth": person.date_of_birth.isoformat(),
        "sex": person.sex,
        "height_cm": "" if person.height_cm is None else f"{person.height_cm:g}",
        "weight_kg": "" if person.weight_kg is None else f"{person.weight_kg:g}",
        "activity_level": person.activity_level,
        "life_stage": person.life_stage,
        "postpartum_since": person.postpartum_since.isoformat() if person.postpartum_since else "",
        "goal": person.goal,
        "energy_surplus_pct": f"{person.energy_surplus_pct:g}",
        "vegetarian_iron": person.vegetarian_iron,
        "milk_mode": {None: "auto", True: "on", False: "off"}[person.milk_feeds_not_logged],
    }


def parse_person_form(form: dict, today: date) -> tuple[dict, dict]:
    """Validate submitted profile fields. Returns (cleaned values, errors by field)."""
    errors: dict[str, str] = {}
    clean: dict = {}

    def number(name: str, lo: float, hi: float, required: bool = False) -> float | None:
        raw = (form.get(name) or "").strip()
        if not raw:
            if required:
                errors[name] = "Required."
            return None
        try:
            x = float(raw)
        except ValueError:
            errors[name] = "Enter a number."
            return None
        if not lo <= x <= hi:
            errors[name] = f"Must be between {lo:g} and {hi:g}."
        return x

    def a_date(name: str, required: bool) -> date | None:
        raw = (form.get(name) or "").strip()
        if not raw:
            if required:
                errors[name] = "Required."
            return None
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            errors[name] = "Enter a valid date."
            return None
        if d > today:
            errors[name] = "Can't be in the future."
        return d

    def choice(name: str, options: dict, default: str) -> str:
        val = form.get(name) or default
        if val not in options:
            errors[name] = "Choose an option."
        return val

    clean["name"] = (form.get("name") or "").strip()
    if not clean["name"]:
        errors["name"] = "Required."
    clean["date_of_birth"] = a_date("date_of_birth", required=True)
    clean["sex"] = choice("sex", SEXES, "")
    clean["height_cm"] = number("height_cm", 30, 250)
    clean["weight_kg"] = number("weight_kg", 1, 300)
    clean["activity_level"] = choice("activity_level", ACTIVITY_LEVELS, "low_active")
    clean["life_stage"] = choice("life_stage", LIFE_STAGES, "general")
    clean["postpartum_since"] = a_date(
        "postpartum_since", required=clean["life_stage"] == "lactation"
    )
    clean["goal"] = choice("goal", GOALS, "maintain")
    surplus = number("energy_surplus_pct", 0, 20)
    clean["energy_surplus_pct"] = 10.0 if surplus is None else surplus
    clean["vegetarian_iron"] = form.get("vegetarian_iron") == "on"
    milk = form.get("milk_mode") or "auto"
    clean["milk_feeds_not_logged"] = {"auto": None, "on": True, "off": False}.get(milk)

    if clean["life_stage"] != "general" and clean["sex"] == "M":
        errors["life_stage"] = "Only applies to females."
    if clean["life_stage"] != "lactation":
        clean["postpartum_since"] = None
    dob = clean["date_of_birth"]
    if (
        clean["life_stage"] != "general"
        and dob
        and "date_of_birth" not in errors
        and age_in_months(dob, today) < 168
    ):
        errors["life_stage"] = "Health Canada pregnancy/breastfeeding values start at age 14."
    return clean, errors


def _save(db, person: Person, clean: dict, today: date) -> None:
    old_weight = person.weight_kg
    for k, v in clean.items():
        setattr(person, k, v)
    db.add(person)
    db.flush()
    if clean["weight_kg"] is not None and clean["weight_kg"] != old_weight:
        entry = db.scalar(
            select(WeightEntry).where(WeightEntry.person_id == person.id, WeightEntry.date == today)
        )
        if entry is None:
            entry = WeightEntry(person_id=person.id, date=today, weight_kg=clean["weight_kg"])
        entry.weight_kg = clean["weight_kg"]
        db.add(entry)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: DbSession, user: CurrentUser):
    people = list(db.scalars(select(Person).order_by(Person.date_of_birth)))
    today = date.today()
    rows = [(p, compute_targets(db, p, today)) for p in people]
    return templates.TemplateResponse(request, "home.html", {"user": user, "rows": rows})


@router.get("/people/new", response_class=HTMLResponse)
def new_person(request: Request, user: CurrentUser):
    values = {"activity_level": "low_active", "life_stage": "general", "goal": "maintain",
              "energy_surplus_pct": "10", "milk_mode": "auto"}  # fmt: skip
    ctx = {"user": user, **_form_context(None, values, {})}
    return templates.TemplateResponse(request, "people/form.html", ctx)


@router.post("/people/new")
async def create_person(request: Request, db: DbSession, user: CurrentUser):
    form = dict(await request.form())
    today = date.today()
    clean, errors = parse_person_form(form, today)
    if errors:
        ctx = {"user": user, **_form_context(None, form, errors)}
        return templates.TemplateResponse(request, "people/form.html", ctx, status_code=422)
    person = Person()
    _save(db, person, clean, today)
    db.commit()
    return RedirectResponse(f"/people/{person.id}/targets", status_code=303)


@router.get("/people/{person_id}/edit", response_class=HTMLResponse)
def edit_person(request: Request, person_id: int, db: DbSession, user: CurrentUser):
    person = get_or_404(db, Person, person_id)
    ctx = {"user": user, **_form_context(person, _values_from(person), {})}
    return templates.TemplateResponse(request, "people/form.html", ctx)


@router.post("/people/{person_id}/edit")
async def update_person(request: Request, person_id: int, db: DbSession, user: CurrentUser):
    person = get_or_404(db, Person, person_id)
    form = dict(await request.form())
    today = date.today()
    clean, errors = parse_person_form(form, today)
    if errors:
        ctx = {"user": user, **_form_context(person, form, errors)}
        return templates.TemplateResponse(request, "people/form.html", ctx, status_code=422)
    _save(db, person, clean, today)
    db.commit()
    return RedirectResponse(f"/people/{person.id}/targets", status_code=303)


@router.get("/people/{person_id}/targets", response_class=HTMLResponse)
def person_targets(request: Request, person_id: int, db: DbSession, user: CurrentUser):
    person = get_or_404(db, Person, person_id)
    pt = compute_targets(db, person)
    key_nutrients = list(
        db.scalars(select(Nutrient).where(Nutrient.is_key).order_by(Nutrient.display_order))
    )
    shown = [pt.targets[n.key] for n in key_nutrients] + [pt.targets["sodium"]]
    ctx = {"user": user, "person": person, "pt": pt, "shown": shown, "nutrients": key_nutrients}
    return templates.TemplateResponse(request, "people/targets.html", ctx)


@router.post("/people/{person_id}/overrides")
def add_override(
    person_id: int,
    db: DbSession,
    user: CurrentUser,
    nutrient_id: Annotated[int, Form()],
    amount: Annotated[float, Form(gt=0)],
    note: Annotated[str, Form(max_length=300)] = "",
):
    person = get_or_404(db, Person, person_id)
    get_or_404(db, Nutrient, nutrient_id)
    existing = next((o for o in person.overrides if o.nutrient_id == nutrient_id), None)
    if existing is None:
        existing = TargetOverride(person_id=person.id, nutrient_id=nutrient_id)
        person.overrides.append(existing)
    existing.amount, existing.note = amount, note.strip()
    db.commit()
    return RedirectResponse(f"/people/{person_id}/targets", status_code=303)


@router.post("/people/{person_id}/overrides/{override_id}/delete")
def delete_override(person_id: int, override_id: int, db: DbSession, user: CurrentUser):
    override = get_or_404(db, TargetOverride, override_id)
    if override.person_id != person_id:
        return RedirectResponse(f"/people/{person_id}/targets", status_code=303)
    db.delete(override)
    db.commit()
    return RedirectResponse(f"/people/{person_id}/targets", status_code=303)
