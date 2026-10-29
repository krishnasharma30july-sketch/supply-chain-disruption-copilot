# Samples

Generated with `make samples`, `make backtest` and `make eval` (or `docker compose --profile tools run --rm tools python -m app.cli samples|backtest|eval`).

- `scenario_1_sao_paulo_rains/`: "Heavy rains are delaying all freight leaving Sao Paulo for the next 5 days"
- `scenario_2_rio_last_mile_strike/`: "Last-mile courier strike in Rio de Janeiro for 3 days"
- `backtest_truckers_strike_2018/`: replay of three real events (May 2018 truckers' strike, Black Friday 2017, Carnival 2018) against actual deliveries, with a control period, a partial-impact factor sweep, Brier scores, reliability tables and leave-one-event-out recalibration
- `evaluation/`: parsing accuracy on 40 scenarios (rules and LLM), retrieval quality (dense vs sparse vs hybrid vs hybrid + cross-encoder rerank) and concept-resolver precision/recall

Each scenario folder contains `impact_summary.md` (full report), `orders.csv` (every order decision), `email_example_1.md` / `email_example_2.md` (sample customer emails with their QA checks) and `run.json`.
