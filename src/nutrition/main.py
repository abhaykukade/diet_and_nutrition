from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

WEB_DIR = Path(__file__).parent / "web"


def create_app() -> FastAPI:
    app = FastAPI(title="Family Nutrition Tracker")
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
