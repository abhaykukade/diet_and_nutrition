import pytest

from nutrition.services.auth import (
    LoginThrottle,
    authenticate,
    create_user,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("s3cret-pass")
    assert h.startswith("scrypt$") and "s3cret-pass" not in h
    assert verify_password("s3cret-pass", h)
    assert not verify_password("wrong", h)
    assert hash_password("s3cret-pass") != h  # salted
    assert not verify_password("x", "garbage")


def test_create_user_rules(session):
    create_user(session, "  Mum ", "longenough")
    assert authenticate(session, "mum", "longenough").username == "mum"
    assert authenticate(session, "MUM", "longenough") is not None
    assert authenticate(session, "mum", "nope") is None
    assert authenticate(session, "nobody", "longenough") is None
    with pytest.raises(ValueError, match="already exists"):
        create_user(session, "mum", "longenough")
    with pytest.raises(ValueError, match="at least 8"):
        create_user(session, "dad", "short")


def test_login_throttle_locks_and_expires():
    t = LoginThrottle(max_failures=3, window_s=60, lockout_s=30)
    for i in range(3):
        assert t.locked_for("u", now=i) == 0
        t.failure("u", now=i)
    assert t.locked_for("u", now=3) == pytest.approx(29)
    assert t.locked_for("u", now=40) == 0
    t.success("u")
    assert t.locked_for("u", now=41) == 0
