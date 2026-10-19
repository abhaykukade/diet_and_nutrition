from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from nutrition.db import get_db
from nutrition.models import AppUser

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

DbSession = Annotated[Session, Depends(get_db)]


class LoginRequired(Exception):
    pass


def current_user(request: Request, db: DbSession) -> AppUser:
    user_id = request.session.get("user_id")
    user = db.get(AppUser, user_id) if user_id else None
    if user is None or not user.is_active:
        request.session.clear()
        raise LoginRequired
    return user


CurrentUser = Annotated[AppUser, Depends(current_user)]


def get_or_404(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404)
    return obj
