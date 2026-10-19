from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from nutrition.services.auth import LoginThrottle, authenticate
from nutrition.web.deps import DbSession, templates

router = APIRouter()
throttle = LoginThrottle()


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login(
    request: Request,
    db: DbSession,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    key = username.strip().lower()
    wait = throttle.locked_for(key)
    if wait:
        error = f"Too many failed attempts. Try again in {int(wait // 60) + 1} min."
        return templates.TemplateResponse(request, "login.html", {"error": error}, status_code=429)
    user = authenticate(db, username, password)
    if user is None:
        throttle.failure(key)
        return templates.TemplateResponse(
            request, "login.html", {"error": "Wrong username or password."}, status_code=401
        )
    throttle.success(key)
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
