"""Точка входа: python -m receiver — запуск HTTP-сервера."""
from __future__ import annotations

from . import create_app
from .config import Config

app = create_app()

if __name__ == "__main__":
    app.run(host=Config.HOST, port=Config.PORT)
