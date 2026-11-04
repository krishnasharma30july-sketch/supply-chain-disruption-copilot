# Evaluation suite

## Parsing (rules)

Field accuracy: **124/124 (100%)**

- All fields correct.

## Retrieval over category documents (top-5)

| Mode | Recall@5 | MRR |
|---|---|---|
| dense | 0.94 | 0.95 |
| sparse | 0.79 | 0.80 |
| hybrid (RRF) | 0.91 | 0.92 |
| hybrid + cross-encoder rerank (default) | 0.95 | 0.94 |

## Concept resolver (lexical + keyword + semantic) -> category filter

Precision **88%**, recall **82%** over 20 concept queries (class-level matches such as 'perishable' are scored separately by the scope filter).

## Search quality against the exact answer (precision@k)

Of the top k results, the share that is truly impacted. Orders and sellers are judged against the structured scope (the filters that decide the run); bulky products against the stored handling profile. “Chance” is the share of impacted documents in the pool, which a random ranking would score.

### “Heavy rains are delaying all freight leaving Sao Paulo for the next 5 days”

Exact scope: 1,409 of 2,120 in-flight orders and 491 sellers.

**Orders** (chance 66%)

| Mode | P@10 | P@50 | P@100 | P@200 |
|---|---|---|---|---|
| dense | 100% | 98% | 95% | 93% |
| sparse | 70% | 76% | 82% | 84% |
| hybrid (RRF) | 90% | 84% | 87% | 90% |
| hybrid + re-ranking (default) | 90% | 92% | 93% | 90% |

**Sellers** (chance 33%): search alone, over all 1,466 sellers.

Scenario-aware query: “Heavy rains are delaying all freight leaving Sao Paulo for the next 5 days”

| Mode | Query | P@10 | P@25 |
|---|---|---|---|
| dense | scenario text | 60% | 72% |
| dense | scenario-aware | 60% | 72% |
| sparse | scenario text | 100% | 100% |
| sparse | scenario-aware | 100% | 100% |
| hybrid (RRF) | scenario text | 90% | 92% |
| hybrid (RRF) | scenario-aware | 90% | 92% |
| hybrid + re-ranking (default) | scenario text | 90% | 96% |
| hybrid + re-ranking (default) | scenario-aware | 90% | 96% |

**Evidence list: the impacted sellers, ranked.** Overlap of the top 10 with the exact ten most exposed sellers (most affected orders, then value).

| Ranking | Overlap@10 |
|---|---|
| search relevance only, scenario text | 1/10 |
| search relevance only, scenario-aware query | 1/10 |
| blended with exposure (weight 0.95, default) | 9/10 |

### “Last-mile courier strike in Rio de Janeiro for 3 days”

Exact scope: 270 of 2,120 in-flight orders and 174 sellers.

**Orders** (chance 13%)

| Mode | P@10 | P@50 | P@100 | P@200 |
|---|---|---|---|---|
| dense | 40% | 22% | 19% | 14% |
| sparse | 60% | 78% | 79% | 76% |
| hybrid (RRF) | 90% | 54% | 49% | 46% |
| hybrid + re-ranking (default) | 70% | 70% | 75% | 46% |

**Sellers** (chance 12%): search alone, over all 1,466 sellers.

Scenario-aware query: “Last-mile courier strike in Rio de Janeiro for 3 days. Parcels in the pipeline now go to Rio de Janeiro (RJ)”

| Mode | Query | P@10 | P@25 |
|---|---|---|---|
| dense | scenario text | 20% | 8% |
| dense | scenario-aware | 40% | 32% |
| sparse | scenario text | 60% | 28% |
| sparse | scenario-aware | 70% | 32% |
| hybrid (RRF) | scenario text | 10% | 24% |
| hybrid (RRF) | scenario-aware | 20% | 36% |
| hybrid + re-ranking (default) | scenario text | 40% | 24% |
| hybrid + re-ranking (default) | scenario-aware | 70% | 56% |

**Evidence list: the impacted sellers, ranked.** Overlap of the top 10 with the exact ten most exposed sellers (most affected orders, then value).

| Ranking | Overlap@10 |
|---|---|
| search relevance only, scenario text | 1/10 |
| search relevance only, scenario-aware query | 0/10 |
| blended with exposure (weight 0.95, default) | 9/10 |

### Heavy or bulky products: “heavy bulky oversized large items furniture home appliances”

**Products** (53 of 6,039 are bulky; chance 1%)

| Mode | P@10 | P@25 |
|---|---|---|
| dense | 80% | 60% |
| sparse | 100% | 100% |
| hybrid (RRF) | 100% | 96% |
| hybrid + re-ranking (default) | 100% | 100% |

## Email quality: AI-written against templates

The same customers, the same fact sheets. Every email is checked by the deterministic fact checker; tone (warmth and clarity, 1 to 5) is rated by the AI reviewer, so read it as a relative signal.

| Writer | Emails | Passed the fact check first time | Warmth | Clarity | Words |
|---|---|---|---|---|---|
| AI (openai/gpt-oss-120b) | 10 | 10/10 | 3.9 (10 rated) | 3.9 | 95.5 |
| Template | 10 | 10/10 | 4.0 (10 rated) | 4.0 | 160.2 |

- Sample: the 10 most severe English-language customers of run c04ff5bc.
- Tone is rated only for AI drafts that passed the fact check (rejected drafts are never sent).
