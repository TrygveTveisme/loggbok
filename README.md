# Loggbok

A personal stock screener, trade list and trade journal for Oslo Børs and US stocks.

- **Screener**: checks a list of stocks against fixed rules (quality at a fair price, own rules for banks and insurers, trend) and ranks the ones that pass by momentum.
- **Trade list**: every second week, says what to sell (15 % trailing stop, broken trend, lost quality), trim (over 15 % of the portfolio) and buy (up to 2 new stocks at about 7 % each, max 30 % per sector).
- **Journal**: logs every transaction with a snapshot of why it was made, keeps daily portfolio snapshots, and shows win rate, average win and loss, and results by exit reason, sector and rule version.

Orders are placed by hand on Nordnet. Start in **paper** mode to test the rules without real money.

All rules live in [`config/rules.yaml`](config/rules.yaml) and the stocks to look at in [`config/universe.yaml`](config/universe.yaml), which pulls in all S&P 500 members (`config/sp500.txt`) and, after `loggbok universe`, every Oslo Børs stock over 1 bn kr (`config/oslo.txt`). Change `version` in the rules file whenever a rule changes.

## Setup (Windows)

```powershell
cd C:\Users\trygv\dev
git clone https://github.com/TrygveTveisme/loggbok.git
cd loggbok
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

## Use

```powershell
loggbok app       # open the dashboard in the browser
loggbok universe  # refresh the S&P 500 and Oslo Børs ticker lists (run now and then)
loggbok screen    # print the ranked candidates and what knocks stocks out
loggbok update    # save today's portfolio snapshot (can run daily from Task Scheduler)
pytest            # run the tests
```

To try the dashboard with made-up data: `set LOGGBOK_FAKE_DATA=1` before `loggbok app`.

Your trades are stored in `data/loggbok.db`. The `data/` folder is in `.gitignore`, so it never goes to GitHub. Back it up yourself.

## How it is built

| File | What it does |
| --- | --- |
| `src/loggbok/data.py` | Market data from Yahoo Finance (yfinance), cached for a day. Swap the source here |
| `src/loggbok/screener.py` | Applies the rules to one stock; ranks the stocks that pass |
| `src/loggbok/tradelist.py` | Builds the sells, trims and buys for a check |
| `src/loggbok/journal.py` | SQLite trade journal: transactions, trades, decision snapshots, skipped signals, daily snapshots |
| `src/loggbok/metrics.py` | Learning-loop numbers: win rate, expectancy, breakdowns, drawdown |
| `src/loggbok/app.py` | Streamlit dashboard |

Market data from Yahoo Finance is unofficial and can change or stop working. This is a personal tool, not investment advice.
