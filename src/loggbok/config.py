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


def load_universe(path: Path | None = None) -> dict[str, list[str]]:
    with open(path or CONFIG_DIR / "universe.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {market: list(tickers or []) for market, tickers in data.items()}


def market_of(ticker: str) -> str:
    return "oslo" if ticker.upper().endswith(".OL") else "us"
