"""Command line: `loggbok screen`, `loggbok update`, `loggbok app`."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .config import load_rules, load_universe


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="loggbok")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("screen", help="run the screener and print the ranked candidates")
    up = sub.add_parser("update", help="save today's portfolio snapshot and update trailing-stop peaks")
    up.add_argument("--mode", choices=["paper", "real"], default="paper")
    sub.add_parser("app", help="open the dashboard")
    args = p.parse_args(argv)

    if args.cmd == "app":
        app = Path(__file__).with_name("app.py")
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app)])

    from .data import YFinanceSource
    from .journal import Journal
    from .pipeline import daily_update, run_screen

    rules, source = load_rules(), YFinanceSource()
    if args.cmd == "screen":
        tickers = [t for ts in load_universe().values() for t in ts]
        res = run_screen(source, tickers, rules, progress=lambda i, n, t: print(f"\r{i + 1}/{n} {t:<10}", end="", flush=True))
        print()
        for e in res.evaluations:
            if e.eligible:
                print(f"{e.rank:>3}  {e.ticker:<10} {e.name[:28]:<28} {e.sector[:22]:<22} momentum {e.momentum:+.1%}")
        print(f"\n{sum(e.eligible for e in res.evaluations)} of {len(res.evaluations)} stocks pass all rules.")
        for tk, err in res.errors.items():
            print(f"error {tk}: {err}")
    elif args.cmd == "update":
        journal = Journal()
        accounts = ["paper"] if args.mode == "paper" else ["ask", "normal"]
        daily_update(journal, source, accounts)
        print("Snapshot saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
