from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from zuyu_app.app import create_app
from zuyu_app.config import Settings


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="development",
        app_version="test",
        db_path=str(tmp_path / "test.db"),
        static_dir=Path(__file__).resolve().parents[1] / "static",
        log_level="ERROR",
        client_log_enabled=False,
    )


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    app = create_app(test_settings)
    with TestClient(app) as test_client:
        yield test_client
