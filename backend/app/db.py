from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import QueuePool

from .config import settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    if url.startswith("postgres"):
        # pg8000 is a pure-python driver (BSD-3): no libpq, no build step.
        return {"poolclass": QueuePool, "pool_size": settings.db_pool_size, "max_overflow": 10,
                "pool_pre_ping": True, "pool_recycle": 1800, "connect_args": {"timeout": 30}}
    return {"connect_args": {"check_same_thread": False, "timeout": 30}, "pool_pre_ping": True}


DATABASE_URL = settings.database_url or f"sqlite:///{settings.db_path}"
IS_POSTGRES = DATABASE_URL.startswith("postgres")

engine = create_engine(DATABASE_URL, **_engine_kwargs(DATABASE_URL))


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    migrate_add_columns()


def db_info() -> dict:
    """Which database this process uses and why (shown by /api/system)."""
    out = {"backend": "postgres" if IS_POSTGRES else "sqlite",
           "url": (DATABASE_URL.split("@")[-1] if IS_POSTGRES else str(settings.db_path)),
           "pool_size": settings.db_pool_size if IS_POSTGRES else None,
           "dialect": engine.dialect.name}
    if IS_POSTGRES:
        try:
            with engine.connect() as c:
                out["server_version"] = str(c.exec_driver_sql("select version()").scalar()).split(",")[0]
        except Exception as e:  # noqa: BLE001 - reported, never fatal
            out["error"] = f"{type(e).__name__}: {e}"[:200]
    return out


def migrate_add_columns() -> list[str]:
    """Forward migration: add columns that exist in the model but not yet in the
    database (works on SQLite and Postgres; new features only add nullable/defaulted
    columns, so this is enough and no migration tool is needed)."""
    from sqlalchemy import inspect, text
    added = []
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl_type = col.type.compile(engine.dialect)
                default = ""
                if col.default is not None and getattr(col.default, "is_scalar", False):
                    v = col.default.arg
                    default = f" DEFAULT {int(v) if isinstance(v, bool) else repr(v) if isinstance(v, str) else v}"
                elif str(col.type) == "JSON":
                    default = " DEFAULT '{}'"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl_type}{default}'))
                added.append(f"{table.name}.{col.name}")
    return added
