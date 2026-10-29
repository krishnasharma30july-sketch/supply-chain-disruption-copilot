# Backtest against real events

For each event the snapshot was rebuilt as of the event's first day with all later timestamps masked, the event was replayed through the same engine the app uses, and predictions were compared with the deliveries that actually happened. Only orders in flight at that moment that were eventually delivered are scored. Brier is the mean squared error of the breach probability (lower is better; 0.25 is a coin flip).

## Nationwide truckers' strike (2018-05-21, 10 days + recovery)

Road freight stopped nationwide for about 10 days (the reference event).

- In-flight orders scored: **2726**; actually delivered late: **560** (20.5%)

| Model | Predicted late | Precision | Recall | F1 | ETA MAE (days) | Brier |
|---|---|---|---|---|---|---|
| Baseline ETA (no disruption awareness) | 119 | 95.8% | 20.4% | 0.34 | 6.2 | 0.123 |
| Disruption-aware ETA (this system, factor 1.0) | 1436 | 32.7% | 83.9% | 0.47 | 9.1 | 0.408 |

Partial-impact factor sweep (A8: share of the window a held shipment actually loses):

| Factor | Predicted late | Precision | Recall | F1 | ETA MAE (days) | Brier |
|---|---|---|---|---|---|---|
| 1 | 1436 | 32.7% | 83.9% | 0.47 | 9.1 | 0.408 |
| 0.75 | 1120 | 37.4% | 74.8% | 0.50 | 7.8 | 0.325 |
| 0.5 | 724 | 45.0% | 58.2% | 0.51 | 6.8 | 0.251 |
| 0.25 | 449 | 53.9% | 43.2% | 0.48 | 6.2 | 0.177 |

## Black Friday capacity crunch (2017-11-24, 5 days + recovery)

Order volume spiked several-fold; modelled as a 5-day nationwide capacity disruption.

- In-flight orders scored: **2292**; actually delivered late: **377** (16.4%)

| Model | Predicted late | Precision | Recall | F1 | ETA MAE (days) | Brier |
|---|---|---|---|---|---|---|
| Baseline ETA (no disruption awareness) | 72 | 91.7% | 17.5% | 0.29 | 5.9 | 0.115 |
| Disruption-aware ETA (this system, factor 1.0) | 378 | 41.5% | 41.6% | 0.42 | 6.5 | 0.196 |

Partial-impact factor sweep (A8: share of the window a held shipment actually loses):

| Factor | Predicted late | Precision | Recall | F1 | ETA MAE (days) | Brier |
|---|---|---|---|---|---|---|
| 1 | 378 | 41.5% | 41.6% | 0.42 | 6.5 | 0.196 |
| 0.75 | 260 | 51.5% | 35.5% | 0.42 | 6.0 | 0.165 |
| 0.5 | 201 | 60.2% | 32.1% | 0.42 | 5.6 | 0.143 |
| 0.25 | 115 | 77.4% | 23.6% | 0.36 | 5.4 | 0.128 |

## Carnival holiday (2018-02-10, 5 days + recovery)

Saturday to Ash Wednesday; modelled as a 5-day nationwide slowdown of pickups and deliveries.

- In-flight orders scored: **3011**; actually delivered late: **519** (17.2%)

| Model | Predicted late | Precision | Recall | F1 | ETA MAE (days) | Brier |
|---|---|---|---|---|---|---|
| Baseline ETA (no disruption awareness) | 129 | 100.0% | 24.9% | 0.40 | 8.6 | 0.098 |
| Disruption-aware ETA (this system, factor 1.0) | 275 | 80.0% | 42.4% | 0.55 | 6.2 | 0.111 |

Partial-impact factor sweep (A8: share of the window a held shipment actually loses):

| Factor | Predicted late | Precision | Recall | F1 | ETA MAE (days) | Brier |
|---|---|---|---|---|---|---|
| 1 | 275 | 80.0% | 42.4% | 0.55 | 6.2 | 0.111 |
| 0.75 | 224 | 88.4% | 38.2% | 0.53 | 6.1 | 0.103 |
| 0.5 | 188 | 93.1% | 33.7% | 0.50 | 6.2 | 0.098 |
| 0.25 | 144 | 99.3% | 27.6% | 0.43 | 6.6 | 0.096 |

## Control period (2018-04-16, no disruption)

- In-flight orders scored: **3168**; actually late: **903** (28.5%)

| Model | Predicted late | Precision | Recall | F1 | ETA MAE (days) | Brier |
|---|---|---|---|---|---|---|
| Baseline ETA | 507 | 99.4% | 55.8% | 0.71 | 6.1 | 0.079 |

## Calibration of breach probabilities (truckers' strike, factor 1.0)

| Predicted p(breach) | Orders | Mean predicted | Observed late rate |
|---|---|---|---|
| 0.0-0.2 | 269 | 12% | 1% |
| 0.2-0.4 | 345 | 30% | 5% |
| 0.4-0.6 | 482 | 49% | 11% |
| 0.6-0.8 | 322 | 69% | 12% |
| 0.8-1.0 | 1308 | 97% | 34% |

Same orders, baseline model (no disruption awareness):

| Predicted p(breach) | Orders | Mean predicted | Observed late rate |
|---|---|---|---|
| 0.0-0.2 | 1853 | 10% | 11% |
| 0.2-0.4 | 525 | 28% | 26% |
| 0.4-0.6 | 140 | 48% | 31% |
| 0.6-0.8 | 56 | 71% | 55% |
| 0.8-1.0 | 152 | 97% | 94% |

## Recalibrated breach probabilities (leave-one-event-out Platt scaling)

The engine's p(breach) answers "if the scenario happens exactly as described" (every in-scope shipment held for the whole window). Real events hit less uniformly, so as a forecast it is over-confident. Platt scaling `p' = sigmoid(a * logit(p) + b)` was fitted on two events and scored on the third:

| Held-out event | Brier baseline | Brier raw (this system) | Brier recalibrated |
|---|---|---|---|
| Nationwide truckers' strike | 0.123 | 0.408 | 0.174 |
| Black Friday capacity crunch | 0.115 | 0.196 | 0.124 |
| Carnival holiday | 0.098 | 0.111 | 0.116 |

Truckers' strike after recalibration (fitted on the other two events):

| Predicted p(breach) | Orders | Mean predicted | Observed late rate |
|---|---|---|---|
| 0.0-0.2 | 1162 | 15% | 7% |
| 0.2-0.4 | 627 | 28% | 20% |
| 0.4-0.6 | 49 | 45% | 59% |
| 0.6-0.8 | 888 | 66% | 37% |

Fitted on all three events: a = 0.25, b = -1.80.

## Summary across events

| Event | Recall baseline -> ours | F1 baseline -> ours | Brier baseline -> raw -> recalibrated | Best-F1 factor |
|---|---|---|---|---|
| Nationwide truckers' strike | 20% -> 84% | 0.34 -> 0.47 | 0.123 -> 0.408 -> 0.174 | 0.5 |
| Black Friday capacity crunch | 18% -> 42% | 0.29 -> 0.42 | 0.115 -> 0.196 -> 0.124 | 0.75 |
| Carnival holiday | 25% -> 42% | 0.40 -> 0.55 | 0.098 -> 0.111 -> 0.116 | 1 |

## Reading the results

- Truckers' strike: recall (truly late orders flagged proactively) moves from 20% to 84% and F1 from 0.34 to 0.47: the disruption-aware model finds far more of the orders that really ended up late.
- Precision moves from 96% to 33%: 966 of the 1436 flagged orders still arrived on time. That is the cost of over-notifying, which is why on-time-but-shifted orders get a heads-up rather than compensation (rule R1).
- F1 improves over the baseline in 3 of 3 events (Nationwide truckers' strike, Black Friday capacity crunch, Carnival holiday).
- Breach probabilities: the raw scenario-conditional probability beats the baseline's Brier in 0 of 3 events, because it assumes the full hold. Out-of-sample recalibration improves it in 2 of 3 events and matches or beats the baseline in 0. The value of disruption awareness is in ranking and recall (who to contact first); the probability shown in the app is the scenario-conditional one, and these numbers show how to read it as a real-world forecast.
- Calibration: the best-F1 factor differs across events (Nationwide truckers' strike 0.5, Black Friday capacity crunch 0.75, Carnival holiday 1), so the default stays at 1.0 (conservative for proactive communication). It is a configurable assumption (`partial_impact_factor`) to re-fit per disruption type as more events are observed.
- The real late-delivery rate was 20.5% during the strike versus 28.5% in the control period, so the control period was not calmer. Olist's early-2018 lateness was already elevated, and promised dates carry large buffers that absorbed much of the strike.
