"""Password hashing (stdlib scrypt) and household user management."""

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from sqlalchemy import select
from sqlalchemy.orm import Session

from nutrition.models import AppUser

# scrypt cost parameters (n=2^15, r=8, p=1: ~32 MiB, tens of ms per hash).
_N, _R, _P = 2**15, 8, 1
_MAXMEM = 64 * 1024 * 1024
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM)
    b64 = base64.b64encode
    return f"scrypt${_N}${_R}${_P}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    expected = base64.b64decode(digest)
    actual = hashlib.scrypt(
        password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
        maxmem=_MAXMEM, dklen=len(expected),
    )  # fmt: skip
    return hmac.compare_digest(actual, expected)


# A hash to verify against when the username doesn't exist, so response time doesn't reveal
# which usernames are valid.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def authenticate(session: Session, username: str, password: str) -> AppUser | None:
    user = session.scalar(select(AppUser).where(AppUser.username == username.strip().lower()))
    if user is None or not user.is_active:
        verify_password(password, _DUMMY_HASH)
        return None
    return user if verify_password(password, user.password_hash) else None


def create_user(session: Session, username: str, password: str) -> AppUser:
    username = username.strip().lower()
    if not username:
        raise ValueError("username is required")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if session.scalar(select(AppUser).where(AppUser.username == username)):
        raise ValueError(f"user {username!r} already exists")
    user = AppUser(username=username, password_hash=hash_password(password))
    session.add(user)
    session.flush()
    return user


def set_password(session: Session, username: str, password: str) -> None:
    user = session.scalar(select(AppUser).where(AppUser.username == username.strip().lower()))
    if user is None:
        raise ValueError(f"no user {username!r}")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    user.password_hash = hash_password(password)


class LoginThrottle:
    """In-memory lockout after repeated failures for a username (per process)."""

    def __init__(self, max_failures: int = 5, window_s: float = 900, lockout_s: float = 300):
        self.max_failures, self.window_s, self.lockout_s = max_failures, window_s, lockout_s
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str, now: float) -> deque[float]:
        q = self._failures[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        return q

    def locked_for(self, key: str, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        q = self._recent(key, now)
        if len(q) >= self.max_failures:
            return max(0.0, q[-1] + self.lockout_s - now)
        return 0.0

    def failure(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._recent(key, now).append(now)

    def success(self, key: str) -> None:
        self._failures.pop(key, None)
