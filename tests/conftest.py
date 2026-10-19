from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

import nutrition.models  # noqa: F401
from nutrition.db import Base, make_engine

FIXTURES = Path(__file__).parent / "fixtures"
CNF_SAMPLE = FIXTURES / "cnf_sample"


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def session(db_url: str):
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    engine.dispose()


@pytest.fixture
def cnf_sample() -> Path:
    return CNF_SAMPLE


@pytest.fixture
def app_factory(db_url, session, tmp_path):
    """Builds the web app against the test database; returns (app, session)."""
    from fastapi.testclient import TestClient

    from nutrition.config import Settings
    from nutrition.db import get_db
    from nutrition.main import create_app
    from nutrition.seeds.load import load_reference_data

    load_reference_data(session)
    session.commit()
    settings = Settings(database_url=db_url, data_dir=tmp_path, secret_key="test-secret")
    app = create_app(settings)
    engine = session.get_bind()

    def _db():
        with sessionmaker(bind=engine, expire_on_commit=False)() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    return lambda: TestClient(app)


@pytest.fixture
def client(app_factory, session):
    from nutrition.services.auth import create_user

    create_user(session, "parent", "correct horse")
    session.commit()
    c = app_factory()
    r = c.post("/login", data={"username": "parent", "password": "correct horse"})
    assert r.status_code == 200  # followed the redirect to "/"
    return c
