"""Приёмник событий с очередью обработки: Flask + SQLAlchemy, очередь — своя, на таблице."""
from __future__ import annotations

from flask import Flask

from .config import Config
from .db import init_app, get_db, close_db


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)

    init_app(app)

    from .routes import bp

    app.register_blueprint(bp)
    app.teardown_appcontext(close_db)
    return app
