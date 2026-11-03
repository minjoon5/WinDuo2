"""Where settings live, and the loggers the reference modules write to."""

from __future__ import annotations

import logging
import os
from pathlib import Path

__all__ = ["APP_DIR", "get_logger"]


def _app_dir() -> Path:
    base = os.environ.get("WINDUO2_HOME") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "WinDuo2"
    return Path.home() / ".winduo2"


APP_DIR = _app_dir()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"winduo2.{name}")
