from datetime import date, timedelta

from sqlalchemy import select

from nutrition.models import Person, WeightEntry
from nutrition.services.auth import create_user
from nutrition.web.routes import auth as auth_routes

ADULT = {
    "name": "Me",
    "date_of_birth": "1990-06-15",
    "sex": "M",
    "height_cm": "178",
    "weight_kg": "70",
    "activity_level": "active",
    "life_stage": "general",
    "goal": "gain_muscle",
    "energy_surplus_pct": "10",
    "milk_mode": "auto",
}


def test_pages_require_login(app_factory):
    c = app_factory()
    for path in ("/", "/people/new", "/people/1/targets"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
    r = c.get("/", headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.status_code == 401 and r.headers["hx-redirect"] == "/login"
    assert c.get("/healthz").status_code == 200
    assert c.get("/static/app.css").status_code == 200


def test_login_failure_and_logout(app_factory, session):
    create_user(session, "parent", "correct horse")
    session.commit()
    c = app_factory()
    r = c.post("/login", data={"username": "parent", "password": "wrong"})
    assert r.status_code == 401 and "Wrong username or password" in r.text
    r = c.post("/login", data={"username": "parent", "password": "correct horse"})
    assert r.status_code == 200 and "Household" in r.text
    c.post("/logout")
    assert c.get("/", follow_redirects=False).status_code == 303


def test_login_is_throttled(app_factory, session, monkeypatch):
    monkeypatch.setattr(auth_routes, "throttle", auth_routes.LoginThrottle(max_failures=2))
    create_user(session, "parent", "correct horse")
    session.commit()
    c = app_factory()
    for _ in range(2):
        c.post("/login", data={"username": "parent", "password": "wrong"})
    r = c.post("/login", data={"username": "parent", "password": "correct horse"})
    assert r.status_code == 429


def test_cross_origin_post_is_blocked(client):
    r = client.post("/people/new", data=ADULT, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/people/new", data=ADULT, headers={"Origin": "http://testserver"})
    assert r.status_code == 200


def test_create_person_shows_targets_and_records_weight(client, session):
    r = client.post("/people/new", data=ADULT)
    assert r.status_code == 200
    assert "Males 31-50 y" in r.text and "Planning aid" not in r.text
    assert "goal" in r.text  # muscle-gain protein/energy goal shown
    person = session.scalar(select(Person).where(Person.name == "Me"))
    assert person.goal == "gain_muscle"
    weights = session.scalars(select(WeightEntry).where(WeightEntry.person_id == person.id))
    assert [w.weight_kg for w in weights] == [70]
    assert "Me" in client.get("/").text


def test_infant_profile_shows_reference_only_and_pediatrician_note(client):
    dob = (date.today() - timedelta(days=245)).isoformat()  # about 8 months old
    baby = {**ADULT, "name": "Baby", "date_of_birth": dob, "height_cm": "70",
            "weight_kg": "8.5", "goal": "maintain"}  # fmt: skip
    r = client.post("/people/new", data=baby)
    assert "Infants 7-12 mo" in r.text
    assert "Planning aid only" in r.text and "pediatrician" in r.text
    assert "reference only" in r.text
    assert "Solids tracking" in client.get("/").text


def test_validation_errors_rerender_form(client, session):
    bad = {**ADULT, "name": "", "weight_kg": "abc", "life_stage": "lactation"}
    r = client.post("/people/new", data=bad)
    assert r.status_code == 422
    assert "Enter a number." in r.text
    assert "Only applies to females." in r.text
    assert session.scalar(select(Person)) is None


def test_edit_and_override(client, session):
    client.post("/people/new", data=ADULT)
    person = session.scalar(select(Person))
    r = client.post(f"/people/{person.id}/edit", data={**ADULT, "weight_kg": "72"})
    assert r.status_code == 200
    session.expire_all()
    assert person.weight_kg == 72

    page = client.get(f"/people/{person.id}/targets").text
    assert "Custom targets" in page
    from nutrition.models import Nutrient

    energy = session.scalar(select(Nutrient).where(Nutrient.key == "energy_kcal"))
    r = client.post(
        f"/people/{person.id}/overrides",
        data={"nutrient_id": energy.id, "amount": "3200", "note": "coach plan"},
    )
    assert "3,200 kcal" in r.text and "coach plan" in r.text
    session.expire_all()
    oid = person.overrides[0].id
    r = client.post(f"/people/{person.id}/overrides/{oid}/delete")
    assert "coach plan" not in r.text
