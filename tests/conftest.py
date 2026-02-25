"""Shared pytest fixtures for clawconductor tests."""

import pytest

import clawconductor.logger as logger_mod


@pytest.fixture(autouse=True)
def _redirect_logs(tmp_path, monkeypatch):
    """Redirect log files to a temp dir for every test.

    Prevents writing to ~/.openclaw/logs during the test suite and keeps
    tests hermetic.
    """
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(logger_mod, "_LOG_DIR", log_dir)
    monkeypatch.setattr(logger_mod, "_DECISION_LOG", log_dir / "clawconductor.log")
    monkeypatch.setattr(logger_mod, "_COST_LOG", log_dir / "model-costs.log")
