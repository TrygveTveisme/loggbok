import io

import pytest

from loggbok import universe


def test_refresh_sp500_converts_class_shares(tmp_path, monkeypatch):
    rows = "Symbol,Security\n" + "".join(f"T{i},Co\n" for i in range(450)) + "BRK.B,Berkshire\n"
    monkeypatch.setattr(universe.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(rows.encode()))
    n = universe.refresh_sp500(tmp_path / "sp.txt")
    text = (tmp_path / "sp.txt").read_text()
    assert n == 451 and "BRK-B" in text and "BRK.B" not in text


def test_refresh_sp500_keeps_old_file_on_bad_download(tmp_path, monkeypatch):
    monkeypatch.setattr(universe.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(b"Symbol\nAAPL\n"))
    (tmp_path / "sp.txt").write_text("OLD\n")
    with pytest.raises(RuntimeError):
        universe.refresh_sp500(tmp_path / "sp.txt")
    assert (tmp_path / "sp.txt").read_text() == "OLD\n"


def test_refresh_oslo_pages_through_screener(tmp_path, monkeypatch):
    import yfinance as yf
    pages = {0: [f"S{i}.OL" for i in range(250)], 250: [f"T{i}.OL" for i in range(20)] + ["NOTOSLO"]}
    calls = []

    def fake_screen(query, offset=0, size=250, sortField=None, sortAsc=None):
        calls.append(offset)
        return {"quotes": [{"symbol": s} for s in pages.get(offset, [])], "total": 271}

    monkeypatch.setattr(yf, "screen", fake_screen)
    n = universe.refresh_oslo(tmp_path / "oslo.txt")
    assert n == 270 and calls == [0, 250] and "NOTOSLO" not in (tmp_path / "oslo.txt").read_text()
