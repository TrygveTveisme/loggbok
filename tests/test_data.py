import pytest

from loggbok.data import Fundamentals, YFinanceSource


class YFRateLimitError(Exception):
    pass


def test_retries_after_rate_limit(tmp_path):
    waits = []
    src = YFinanceSource(cache_dir=tmp_path, sleep=waits.append)
    calls = {"n": 0}

    def flaky(ticker):
        calls["n"] += 1
        if calls["n"] < 3:
            raise YFRateLimitError("Too Many Requests. Rate limited.")
        return Fundamentals(ticker=ticker, currency="NOK")

    src._fetch_fundamentals = flaky
    assert src.fundamentals("EQNR.OL").currency == "NOK"
    assert waits == [10, 30]
    # Cached now: no new call
    assert src.fundamentals("EQNR.OL").ticker == "EQNR.OL" and calls["n"] == 3


def test_gives_up_after_retries_and_other_errors_fail_fast(tmp_path):
    waits = []
    src = YFinanceSource(cache_dir=tmp_path, sleep=waits.append)
    src._fetch_fundamentals = lambda t: (_ for _ in ()).throw(YFRateLimitError("Too Many Requests"))
    with pytest.raises(YFRateLimitError):
        src.fundamentals("X.OL")
    assert waits == [10, 30, 90]
    waits.clear()
    src._fetch_fundamentals = lambda t: (_ for _ in ()).throw(KeyError("currency"))
    with pytest.raises(KeyError):
        src.fundamentals("Y.OL")
    assert waits == []
