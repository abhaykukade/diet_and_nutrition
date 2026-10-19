from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from nutrition.config import Settings, get_secret_key, get_settings
from nutrition.web.deps import LoginRequired, templates
from nutrition.web.routes import auth, people

WEB_DIR = Path(__file__).parent / "web"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def age_text(months: int) -> str:
    if months < 24:
        return f"{months} month{'s' if months != 1 else ''}"
    return f"{months // 12} years"


def num(x: float | None) -> str:
    """1234.0 -> "1,234"; 75.4 -> "75.4"; 0.5 -> "0.5"."""
    if x is None:
        return ""
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:g}"


templates.env.globals["age_text"] = age_text
templates.env.filters["num"] = num


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Family Nutrition Tracker", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        # CSRF defence alongside SameSite=Lax cookies: browsers send Origin (or Referer) on
        # form posts, and it must match the host serving the app.
        if request.method in UNSAFE_METHODS:
            source = request.headers.get("origin") or request.headers.get("referer")
            if source and urlsplit(source).netloc != request.headers.get("host"):
                return PlainTextResponse("Cross-origin request blocked", status_code=403)
        return await call_next(request)

    # Added after the middleware above, so it wraps it: sessions are available everywhere.
    app.add_middleware(
        SessionMiddleware,
        secret_key=get_secret_key(settings),
        session_cookie="nutrition_session",
        max_age=settings.session_max_age_days * 24 * 3600,
        same_site="lax",
        https_only=settings.secure_cookies,
    )

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, _exc: LoginRequired):
        if request.headers.get("hx-request"):
            return PlainTextResponse("", status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)

    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(auth.router)
    app.include_router(people.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
