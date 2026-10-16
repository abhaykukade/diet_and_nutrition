from alembic import command

from nutrition.cli import alembic_config


def test_migrations_build_schema_matching_models(db_url):
    cfg = alembic_config(db_url)
    command.upgrade(cfg, "head")
    # Fails if models have changed without a corresponding migration.
    command.check(cfg)
