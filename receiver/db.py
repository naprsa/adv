"""SQLAlchemy database setup and models for event receiver."""

from __future__ import annotations

import os
from pathlib import Path

from flask import current_app, g
from sqlalchemy import (
    Engine,
    create_engine,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(nullable=False)
    payload: Mapped[str] = mapped_column(nullable=False)  # JSON stored as TEXT
    received_at: Mapped[str] = mapped_column(nullable=False)  # ISO-8601, UTC


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(nullable=False, unique=True)
    status: Mapped[str] = mapped_column(nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    next_run_at: Mapped[float] = mapped_column(nullable=False)  # epoch
    locked_at: Mapped[float | None] = mapped_column(default=None)
    last_error: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[float] = mapped_column(nullable=False)
    updated_at: Mapped[float] = mapped_column(nullable=False)


def build_engine_and_factory(db_path: str) -> tuple[Engine, sessionmaker]:
    """Engine + sessionmaker, прагмы (WAL, foreign keys) и идемпотентное create_all.

    Одна точка сборки БД — её используют и Flask-приложение (init_app),
    и воркер (standalone-запуск, где Flask нет).
    """
    if db_path != ":memory:":
        db_path = str(Path(db_path).expanduser())
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"timeout": 5.0},
    )

    # Set SQLite pragmas via a connection context
    with engine.begin() as conn:
        conn.execute(text("PRAGMA journal_mode = WAL"))
        conn.execute(text("PRAGMA foreign_keys = ON"))

    # Create tables idempotently (same effect as the original SCHEMA)
    Base.metadata.create_all(bind=engine)

    return engine, sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_app(app) -> None:
    """Initialize SQLAlchemy with the Flask application."""
    db_path = app.config["DATABASE"]
    if db_path != ":memory:":
        db_path = str(Path(db_path).expanduser())
        app.config["DATABASE"] = db_path

    engine, session_factory = build_engine_and_factory(db_path)

    # Store on app object so fixtures can access them
    app.db_engine = engine
    app.db_session_factory = session_factory


def get_db() -> Session:
    """Get a SQLAlchemy session for the current request (lazy, closed in teardown)."""
    if "db" not in g:
        # Try to get session factory from current app context
        try:
            session_factory = current_app.db_session_factory
        except RuntimeError:
            # No app context — create a temporary one for test compatibility
            from flask import Flask

            # This won't fully work without proper app setup, so raise
            raise RuntimeError("Database not initialized. Call create_app() first.")
        g.db = session_factory()
    return g.db


def get_session() -> Session:
    """Get a new session for worker/background usage (not request-scoped).

    Must be called within a Flask application context.
    """
    session_factory = current_app.db_session_factory
    return session_factory()


def close_db(_exc: BaseException | None = None) -> None:
    """Remove the session at the end of the request context."""
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()
