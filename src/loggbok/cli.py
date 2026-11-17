"""Command line: `loggbok screen`, `loggbok update`, `loggbok app`."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .config import all_tickers, load_rules, load_universe


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="loggbok")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("screen", help="run the screener and print the ranked candidates")
    up = sub.add_parser("update", help="save today's portfolio snapshot and update trailing-stop peaks")
    up.add_argument("--mode", choices=["paper", "real"], default="paper")
    sub.add_parser("app", help="open the dashboard")
    sub.add_parser("universe", help="refresh the S&P 500 and Oslo Børs ticker lists in config/")
    args = p.parse_args(argv)

    if args.cmd == "app":
        app = Path(__file__).with_name("app.py")
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app)])

    if args.cmd == "universe":
        from .universe import refresh_oslo, refresh_sp500
        for name, fn in (("S&P 500", refresh_sp500), ("Oslo Børs", refresh_oslo)):
            try:
                print(f"{name}: {fn()} tickers saved")
            except Exception as e:
                print(f"{name}: not refreshed ({type(e).__name__}: {e})")
        print(f"Universe now has {len(all_tickers(load_universe()))} tickers.")
        return 0

    from .data import YFinanceSource
    from .journal import Journal
    from .pipeline import daily_update, run_screen

    rules, source = load_rules(), YFinanceSource()
    if args.cmd == "screen":
        tickers = all_tickers(load_universe())
        res = run_screen(source, tickers, rules, progress=lambda i, n, t: print(f"\r{i + 1}/{n} {t:<10}", end="", flush=True))
        print()
        for e in res.evaluations:
            if e.eligible:
                bonus = f"  + {', '.join(e.bonuses)}" if e.bonuses else ""
                print(f"{e.rank:>3}  {e.ticker:<10} {e.name[:28]:<28} {e.sector[:22]:<22} momentum {e.momentum:+.1%}{bonus}")
        print(f"\n{sum(e.eligible for e in res.evaluations)} of {len(res.evaluations)} stocks pass all rules.")
        from .screener import rule_summary
        print("\nWhat knocks stocks out (failed / missing data / checked):")
        for c in rule_summary(res.evaluations):
            if c["failed"] or c["missing"]:
                print(f"  {c['group']:<10} {c['rule']:<26} {c['failed']:>4} {c['missing']:>4} {c['checked']:>5}")
        for tk, err in res.errors.items():
            print(f"error {tk}: {err}")
        if res.errors:
            print(f"\n{len(res.errors)} stocks could not be fetched. Run `loggbok screen` again later: "
                  "stocks fetched today are cached, so only these are tried again.")
    elif args.cmd == "update":
        journal = Journal()
        accounts = ["paper"] if args.mode == "paper" else ["ask", "normal"]
        daily_update(journal, source, accounts)
        print("Snapshot saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
