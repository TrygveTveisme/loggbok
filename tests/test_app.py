"""Smoke test: the dashboard starts and shows all tabs, using made-up data and a temporary database."""
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest


@pytest.fixture(autouse=True)
def fresh_caches():
    st.cache_resource.clear()
    st.cache_data.clear()


def test_app_runs_with_fake_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGGBOK_FAKE_DATA", "1")
    monkeypatch.setenv("LOGGBOK_DB", str(tmp_path / "test.db"))
    at = AppTest.from_file("../src/loggbok/app.py", default_timeout=60).run()
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs] == ["Screener", "Trade list", "Portfolio", "Journal"]


def test_app_shows_buys_after_deposit(tmp_path, monkeypatch):
    from loggbok.journal import Journal

    db = tmp_path / "test.db"
    Journal(db).deposit("paper", 100_000)
    monkeypatch.setenv("LOGGBOK_FAKE_DATA", "1")
    monkeypatch.setenv("LOGGBOK_DB", str(db))
    at = AppTest.from_file("../src/loggbok/app.py", default_timeout=60).run()
    assert not at.exception, at.exception
    texts = " ".join(m.value for m in at.markdown)
    assert "BUY" in texts
