"""`loggbok backtest`: compare stop-loss variants on past prices and save the details."""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from .backtest import run_backtest
from .config import DATA_DIR, all_tickers, load_rules, load_universe

VARIANTS = [
    ("15 % trailing (current)", dict(trailing=True, stop=0.15)),
    ("15 % fixed from purchase", dict(trailing=False, stop=0.15)),
    ("10 % trailing", dict(trailing=True, stop=0.10)),
    ("20 % trailing", dict(trailing=True, stop=0.20)),
]


def _pct(x):
    return "   n/a" if x is None else f"{x:+6.1%}"


def run(years: int = 5, quality_only: bool = False, source=None) -> int:
    from .pipeline import run_screen

    if source is None:
        from .data import YFinanceSource
        source = YFinanceSource()
    rules = load_rules()
    tickers = all_tickers(load_universe())
    print("Screening today's universe (cached if already run today)...")
    screen = run_screen(source, tickers, rules)
    evals = screen.by_ticker()
    sectors = {tk: e.sector for tk, e in evals.items()}
    if quality_only:
        tickers = [tk for tk, e in evals.items() if e.group_passed.get("universe") and e.passes_quality]
        print(f"Using {len(tickers)} stocks that pass today's quality rules (results are flattered).")
    else:
        tickers = [tk for tk, e in evals.items() if e.group_passed.get("universe")]
        print(f"Using {len(tickers)} stocks that pass the size and liquidity rules (trend and selling rules only).")

    print(f"Downloading {years + 1} years of prices...")
    closes = source.prices(tickers, period=f"{years + 1}y")
    fx = source.prices(["USDNOK=X"], period=f"{years + 1}y")
    fx_series = fx["USDNOK=X"] if "USDNOK=X" in fx else None
    start = (pd.Timestamp.today() - pd.DateOffset(years=years)).date().isoformat()

    out_dir = DATA_DIR / "backtest" / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n{'Variant':<26} {'CAGR':>7} {'total':>7} {'max drop':>9} {'trades':>7} {'win %':>6} {'avg win':>8} {'avg loss':>9}")
    rows = []
    bench_line = None
    for name, kw in VARIANTS:
        res = run_backtest(closes, rules, fx_usdnok=fx_series, sectors=sectors, start=start, **kw)
        sm = res.summary()
        rows.append({"variant": name, **{k: v for k, v in sm.items() if k != "exit_reasons"}, **{f"exits_{k}": v for k, v in sm["exit_reasons"].items()}})
        win = "   n/a" if sm["win_rate"] is None else f"{sm['win_rate']:6.0%}"
        print(f"{name:<26} {_pct(sm['cagr']):>7} {_pct(sm['total_return']):>7} {_pct(sm['max_drawdown']):>9} {sm['trades']:>7} {win:>6} {_pct(sm['avg_win']):>8} {_pct(sm['avg_loss']):>9}")
        slug = name.split(" (")[0].replace(" ", "_").replace("%", "pct")
        res.equity.to_csv(out_dir / f"equity_{slug}.csv", header=["value_nok"])
        pd.DataFrame([t.__dict__ | {"result_pct": t.result_pct} for t in res.trades]).to_csv(out_dir / f"trades_{slug}.csv", index=False)
        if bench_line is None and "benchmark_cagr" in sm:
            bench_line = (sm["benchmark_cagr"], sm["benchmark_return"], sm["benchmark_max_drawdown"])
    if bench_line:
        print(f"{'Buy and hold same stocks':<26} {_pct(bench_line[0]):>7} {_pct(bench_line[1]):>7} {_pct(bench_line[2]):>9}")
    pd.DataFrame(rows).to_csv(out_dir / "summary.csv", index=False)
    (out_dir / "settings.json").write_text(json.dumps({"years": years, "quality_only": quality_only,
                                                       "stocks": len(tickers), "rule_version": rules.get("version")}, indent=2))
    print(f"\nDetails saved in {out_dir}")
    print("Note: orders fill at the close on each check day, and fees follow `fees` in rules.yaml.")
    return 0
