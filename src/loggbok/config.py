"""Load the rules and the stock universe from the YAML files in config/."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"


def load_rules(path: Path | None = None) -> dict[str, Any]:
    with open(path or CONFIG_DIR / "rules.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_ticker_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.split("#")[0].strip().upper() for ln in lines if ln.split("#")[0].strip()]


def load_universe(path: Path | None = None) -> dict[str, list[str]]:
    """Tickers per source: the lists in universe.yaml plus every file under `include`."""
    path = path or CONFIG_DIR / "universe.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    includes = data.pop("include", []) or []
    out = {name: list(tickers or []) for name, tickers in data.items()}
    for fname in includes:
        out[fname] = read_ticker_file(path.parent / fname)
    return out


def all_tickers(universe: dict[str, list[str]]) -> list[str]:
    return list(dict.fromkeys(t for ts in universe.values() for t in ts))


def market_of(ticker: str) -> str:
    return "oslo" if ticker.upper().endswith(".OL") else "us"
