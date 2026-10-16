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
