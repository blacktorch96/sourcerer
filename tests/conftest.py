from __future__ import annotations

from pathlib import Path

import pytest

from ytdigest.config import Config
from ytdigest.db.schema import init_db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config()
    c.paths.feeds_file = tmp_path / "feeds.txt"
    c.paths.database = tmp_path / "db.sqlite3"
    c.paths.output_dir = tmp_path / "out"
    c.paths.log_file = tmp_path / "logs" / "log.txt"
    c.asr.enabled = False
    return c


@pytest.fixture
def conn(cfg: Config):
    connection = init_db(cfg.paths.database)
    yield connection
    connection.close()
