# Evaluation suite

## Parsing (rules)

Field accuracy: **124/124 (100%)**

- All fields correct.

## Parsing (llm)

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
