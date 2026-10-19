# Family Nutrition Tracker

A small web app (phone-friendly) for logging what each person in the household eats,
comparing intake with Health Canada Dietary Reference Intakes, and, later, suggesting foods
that fill nutrient gaps.

**Status:** Phase 1 in progress. Done so far: project skeleton, the Canadian Nutrient File
(CNF) importer, Health Canada DRI reference data, login, household profiles and personal
targets.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                      # create .venv and install dependencies
uv run nutrition init-db     # create/migrate the SQLite database (data/nutrition.db)
uv run nutrition seed        # load reference data (nutrients, DRIs)
uv run nutrition create-user mum     # add a login (asks for a password; repeat per person)
uv run nutrition serve       # http://<this-computer's-LAN-IP>:8000 from your phone
```

Then open the app on your phone (same Wi-Fi), log in and add each household member.

### Logins and security

- Logins are household accounts. Use `nutrition set-password NAME` to change a password.
- Passwords are stored as salted scrypt hashes. Sessions use a signed cookie (30 days,
  `HttpOnly`, `SameSite=Lax`). Form posts from other sites are rejected. Five failed logins in a
  row lock that username for 5 minutes.
- The cookie-signing key is generated on first run and kept in `data/secret_key`. Set
  `NUTRITION_SECRET_KEY` to use your own.
- The app serves plain HTTP, which is fine on a home network you trust. If you expose it beyond
  that, put it behind HTTPS (e.g. Caddy or Tailscale) and set `NUTRITION_SECURE_COOKIES=true`.

The database location can be changed with `NUTRITION_DATABASE_URL`
(e.g. `NUTRITION_DATABASE_URL=sqlite:////srv/nutrition/nutrition.db`), or in a `.env` file.

## Importing the Canadian Nutrient File

1. Download the CNF CSV files from Health Canada:
   <https://www.canada.ca/en/health-canada/services/food-nutrition/healthy-eating/nutrient-data/canadian-nutrient-file-2015-download-files.html>
   Use the **Entire database: CSV** zip (`cnf-fcen-csv.zip`, CNF 2015, 5,690 foods). You don't
   need the separate "update files" zip: it only lists changes since the 2010 edition, and
   those are already in the full database.
2. Import the zip as downloaded, or the folder you extracted it into:

   ```bash
   uv run nutrition import-cnf data/cnf-fcen-csv.zip
   ```

The import is safe to re-run (for example, when a newer release comes out). Existing foods keep
their ids, so earlier log entries stay valid. Foods dropped from the new release are marked
inactive, not deleted.

The importer prints a report:

- **Nutrient mapping:** which CNF nutrient feeds each of the app's nutrients.
- **Coverage:** how many foods have a value for each nutrient. In CNF 2015, most nutrients are
  at 80–100%; the exception is the total omega-3 field (about 35%).

The mapping lives in `src/nutrition/seeds/cnf_nutrient_map.csv`. Each row gives a CNF
`NutrientID`, the expected unit and words its name must contain. If the CNF files don't match,
the import stops and writes nothing. Watch out: CNF's `NutrientID` (used by the amounts file)
differs from its `NutrientCode` for some nutrients, e.g. folate DFE is ID 815 / code 435. All
rows have been checked against the CNF 2015 release.

Raw downloads and the database live in `data/` and are not committed.

## Dietary Reference Intakes

```bash
uv run nutrition seed            # load nutrients, DRIs, AMDRs and energy equations
```

The DRI targets come from Health Canada's
[DRI tables](https://www.canada.ca/en/health-canada/services/food-nutrition/healthy-eating/dietary-reference-intakes/tables.html).
They're stored in three committed seed files in `src/nutrition/seeds/`:

| File | Contents |
|---|---|
| `dri_values.csv` | EAR / RDA / AI / UL / CDRR for every life-stage group, for the tracked nutrients |
| `amdr.csv` | Acceptable Macronutrient Distribution Ranges (% of energy) |
| `eer_equations.csv` | Estimated Energy Requirement equations (age, sex, height, weight, activity level, pregnancy, breastfeeding) |

No value is typed by hand. `nutrition build-dri-seed` downloads the official pages and parses
their tables to regenerate the files. Every row records:

- the source URL, table caption and the page's "date modified"
- the footnotes that apply to it, e.g. "the requirement for iron is 1.8 times higher for
  vegetarians"

When the source gives no value ("ND", not determinable), the amount is left empty and the
`status` column says why. Rows missing from the source tables get `status = missing_from_source`.
The command lists both kinds of flagged values when it finishes.

## Personal targets

Each profile's targets come from the Health Canada DRI group matching their age (in completed
months), sex and pregnancy/breastfeeding status, so the baby's targets change on his first
birthday without anyone editing anything.

| Nutrient(s) | How the target is set |
|---|---|
| Most nutrients | RDA, or AI where there's no RDA |
| Protein | RDA per kg × body weight |
| Calories | Health Canada energy equation (age, height, weight, activity level; breastfeeding uses the postpartum equations, picked from the baby's birth date) |
| Carbs, protein, fat | Acceptable range as % of calories, also shown |
| Upper limits | UL. For sodium, the CDRR (2,300 mg for adults). The folate UL is checked against synthetic folic acid only, as Health Canada specifies. |

Adjustments, each shown with its source on the targets page:

- **Muscle-gain goal:**
  - Protein 1.6 g/kg/day, based on
    [Morton et al. 2018](https://bjsm.bmj.com/content/52/6/376) (no extra gain above ~1.62 g/kg)
    and [Iraki et al. 2019](https://pmc.ncbi.nlm.nih.gov/articles/PMC6680710/) (1.6–2.2 g/kg).
  - Calories: the energy estimate +10% (adjustable up to 20%), per Iraki et al. Check it against
    the weight trend.
- **Vegetarian iron:** an optional per-person switch multiplies the iron target by 1.8 (Health
  Canada footnote). It's off by default because the household eats chicken and fish.
- **Custom targets:** any nutrient's target can be replaced by hand on the targets page.

Pregnancy calorie targets aren't calculated: the equations need gestation week and pre-pregnancy
BMI. Set a custom target instead.

**Infants:** under 12 months, or whenever "solids tracking" is switched on, the targets are shown
for reference only. Breast milk and formula aren't logged, so the dashboard won't report targets
as met or unmet.

## Development

```bash
uv run pytest                # run tests
uv run ruff check . && uv run ruff format --check .
uv run uvicorn nutrition.main:create_app --factory --reload   # dev server, auto-reload
```

Tests use a tiny synthetic CNF-format sample (`tests/fixtures/cnf_sample/`, regenerated by
`tests/fixtures/make_cnf_sample.py`). Its values are made up and are never used as real data.

Schema changes go through Alembic:

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run nutrition init-db
```

## Project layout

```
src/nutrition/
  config.py, db.py, main.py   settings, SQLite engine (WAL, foreign keys), FastAPI app
  cli.py                      `nutrition` command (init-db, import-cnf, ...)
  models/                     SQLAlchemy models
  importers/base.py           source-agnostic import pipeline (CNF now, USDA FDC later)
  importers/cnf.py            CNF CSV reader
  sources/hc_dri.py           builds the DRI seed files from Health Canada's DRI tables
  seeds/                      reference data: nutrients, CNF nutrient map, DRIs, AMDRs, EER equations
  services/                   targets (DRIs, energy, goals), auth (password hashing, throttling)
  web/                        routes, Jinja2 templates, static files (Pico.css, htmx; vendored)
migrations/                   Alembic migrations
tests/                        pytest suite
```

## Disclaimer

The infant section is a planning aid only. Confirm any change to your baby's diet with your
pediatrician.
