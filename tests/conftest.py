"""Shared fixtures for the core test suite."""
from __future__ import annotations

import pytest

from tests.helpers import install_fake_plugin


@pytest.fixture
def fake_plugin(monkeypatch):
    """Two made-up system setups (X1 long, X2 short) in place of whatever plugin
    is or is not installed. Yields their codes."""
    return install_fake_plugin(monkeypatch)


@pytest.fixture(autouse=True)
def _allow_testclient_host(monkeypatch):
    """TestClient sends Host: testserver, which the local-only guard refuses.
    tests/test_local_guard.py clears this to test the real allowlist."""
    monkeypatch.setenv("SCANNER_ALLOWED_HOSTS", "testserver")


@pytest.fixture(autouse=True)
def _no_ov_picks_dir(monkeypatch):
    # fork: OV picks card (ashwinurao/edge-scanner#20). scanner/data/alpaca.py loads .env on
    # import, so a developer's OV_PICKS_DIR would start the day watchlist and notice in every
    # test that starts the real app, on the real data/ folder. Tests set it themselves.
    monkeypatch.delenv("OV_PICKS_DIR", raising=False)
