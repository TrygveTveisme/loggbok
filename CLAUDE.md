# Loggbok

Project note: `Projects in progress/Stock Tracker/Stock Tracker.md` in the Second Brain (vault path in the global CLAUDE.md).
Read it at the start of each session. It holds the strategy, decisions and open questions.

## Project facts
- Repo: https://github.com/TrygveTveisme/loggbok (private, personal account)
- Stack: Python 3.10+, pandas, yfinance, SQLite, Streamlit
- Build / run: `pip install -e ".[dev]"`, then `loggbok app`
- Test: `pytest`

## Project rules
- This project is personal. Never connect it to, or request access to, any work organization on GitHub or elsewhere.
- Rules are data: change thresholds in `config/rules.yaml`, never hard-code them, and bump `version` on any rule change.
- Every buy and sell must be logged with a decision snapshot; the learning loop depends on it.
- `data/` (the journal and caches) must never be committed.
- Tests must not need internet: use `loggbok.fake.FakeSource`.
