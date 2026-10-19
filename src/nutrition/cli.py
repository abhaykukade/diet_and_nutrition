"""Command-line entry point: ``nutrition <command>`` (or ``python -m nutrition.cli``)."""

import argparse
import getpass
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from nutrition.config import PROJECT_ROOT, get_settings
from nutrition.db import get_session_factory
from nutrition.importers.base import (
    SEEDS_DIR,
    ImportValidationError,
    load_nutrient_map,
    write_bundle,
)
from nutrition.importers.cnf import CNF_NUTRIENT_MAP, read_cnf
from nutrition.seeds.load import load_reference_data
from nutrition.services.auth import create_user, set_password
from nutrition.sources import hc_dri


def alembic_config(database_url: str | None = None) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url or get_settings().database_url)
    return cfg


def cmd_init_db(_args: argparse.Namespace) -> int:
    get_settings().data_dir.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(), "head")
    print("Database is up to date.")
    return 0


def cmd_import_cnf(args: argparse.Namespace) -> int:
    path = Path(args.path)
    print(f"Reading CNF files from {path} ...")
    bundle = read_cnf(path, version=args.version)
    with get_session_factory()() as session:
        try:
            report = write_bundle(session, bundle, load_nutrient_map(CNF_NUTRIENT_MAP))
        except ImportValidationError as e:
            session.rollback()
            print(f"Import aborted, nothing was written.\n{e}", file=sys.stderr)
            return 1
        session.commit()
    print(report.format())
    return 0


def cmd_seed(_args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        counts = load_reference_data(session)
        session.commit()
    print("Loaded reference data: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


def cmd_build_dri_seed(args: argparse.Namespace) -> int:
    html_dir = Path(args.html_dir) if args.html_dir else None
    out_dir = Path(args.out_dir)
    result = hc_dri.build(out_dir, html_dir)
    for name, rows in result.items():
        print(f"wrote {out_dir / (name + '.csv')} ({len(rows)} rows)")
    flagged = [r for r in result["dri_values"] if r["status"] not in ("ok", "not_determinable")]
    print(f"{len(flagged)} values flagged (missing or unparsed in the source):")
    for r in flagged:
        print(f"  {r['nutrient_key']} {r['age_label']} {r['value_type']}: {r['status']}")
    return 0


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    pw = getpass.getpass("Password: ")
    if pw != getpass.getpass("Repeat password: "):
        raise ValueError("passwords don't match")
    return pw


def cmd_create_user(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        try:
            create_user(session, args.username, _read_password(args))
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        session.commit()
    print(f"Created user {args.username.strip().lower()!r}.")
    return 0


def cmd_set_password(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        try:
            set_password(session, args.username, _read_password(args))
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        session.commit()
    print("Password updated.")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("nutrition.main:create_app", factory=True, host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nutrition")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="create or migrate the database")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("import-cnf", help="import the Canadian Nutrient File (zip or directory)")
    p.add_argument("path", help="path to the CNF .zip or the folder of extracted CSVs")
    p.add_argument("--version", default="2015", help="CNF release label (default: 2015)")
    p.set_defaults(func=cmd_import_cnf)

    p = sub.add_parser("seed", help="load reference data (nutrients, DRIs, AMDRs, EER equations)")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser(
        "build-dri-seed", help="regenerate the DRI seed CSVs from Health Canada's DRI tables"
    )
    p.add_argument("--html-dir", help="read saved copies of the pages instead of fetching")
    p.add_argument("--out-dir", default=str(SEEDS_DIR), help="where to write the CSVs")
    p.set_defaults(func=cmd_build_dri_seed)

    for name, func, help_ in (
        ("create-user", cmd_create_user, "add a household login"),
        ("set-password", cmd_set_password, "change a login's password"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("username")
        p.add_argument("--password-stdin", action="store_true", help="read password from stdin")
        p.set_defaults(func=func)

    p = sub.add_parser("serve", help="run the web app")
    p.add_argument("--host", default="0.0.0.0", help="0.0.0.0 = reachable from phones on your LAN")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
