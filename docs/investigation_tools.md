# Investigation tools (Phase 9A) — contract table and reference

Deterministic, read-only investigation layer: **6 installed GSQL queries** (`graph/queries/*.gsql`, graph `FraudInvestigation`, v1 syntax) plus a **Python wrapper** (`src/fraud_tools/`) that adds sentinel handling, as-of/leak guards, hub-tier logic, the validated signal rules and the policy context. No LLM, no GraphRAG, no embeddings.

Sources of truth: `docs/implementation_spec.md` (§4 temporal rules, §5 hub rules, §6 evidence hierarchy, §7 queries, §8 policy, §12 never-expose, E1–E18), `docs/signal_validation.md`, `scripts/analysis/` oracles, and the hackathon brief `README.md`.

## Session model (why the LLM cannot supply `as_of` or GSQL)

```
runner (trusted) ──► InvestigationSession(as_of = case.opened_at epoch)   # fixed for the session
LLM (later)       ──► session.get_transaction_context("3514030") ...       # tool arguments contain ids only
session           ──► whitelisted GET /restpp/query/FraudInvestigation/fi_*   # no /gsql endpoint, no other graph
```
* `as_of` is a constructor argument held by the runner; **no tool has an `as_of` parameter.**
* The client can only call the six `fi_*` installed queries with fixed parameter names; there is no raw-GSQL, no `Transaction_Fraud`, no schema/loader/admin call.
* Every result is checked (`max_epoch_seen ≤ as_of`, `close_epoch ≤ as_of`); a violation raises `LeakError` and nothing is returned.

## Contract table

`hub guard` = which entities are never expanded. Snapshot class (`snap_class`) is only a pre-filter; **as-of degree is recomputed** and compared to `max_customers` (default 100; sharing evidence tiers ≤ 5 strong / 6–20 moderate / > 20 none, spec §5.1).

| Tool | Inputs (agent) | Outputs | as_of behaviour | Hub guard | Evidence returned |
|---|---|---|---|---|---|
| `get_transaction_context` | `txn_id` | transaction attributes (sentinels → null), card, customer, device, purchaser/recipient email, billing region, previous/next txn on the card with gaps, temporal context (hour, weekday, seconds since previous), `model_score` (context only) | txn visible iff `epoch ≤ as_of` (else `FutureTransactionError`); device/email/region edges `epoch ≤ as_of`; next txn only if `epoch ≤ as_of` | none (no expansion) | identity flags (`id_15`, `proxy_type`, `device_type`), region/country, email domains |
| `get_customer_history` | `customer_id`, `lookback_days` (≤ 200) | cards with as-of counts/amount stats, customer totals, product/channel mix, **visible closed cases** | cards with `first_epoch ≤ as_of`; txns `epoch ≤ as_of`; cases `close_epoch ≤ as_of` | none | prior case history (pattern, outcome, exposure, report_filed), spend profile |
| `get_card_history` | `card_id`, `hours` (≤ 4,800), `max_rows` (≤ 500) | recent transactions (newest first, with device ids), baseline (n, mean/std of log-amount, min/max, product/channel/hour/region histograms, distinct devices with first seen), truncation flag | rows/baseline `epoch ≤ as_of`; baseline strictly **before** the flagged txn when `before_txn_id` given | none | amount/timing/channel/region context |
| `find_shared_devices` | `card_id` (or `txn_id`), `days` (≤ 30) | per device the card used: as-of `customers`, `txns`, `new_share`, `proxy_share`, `n_known`, snapshot class, **sharing tier**, `ring_suspect`, other customers/cards on it (bounded), visible confirmed-fraud customers/cases; `skipped_hubs` | all counts from edges `epoch ≤ as_of`; fraud counted only for cases with `close_epoch ≤ as_of` | NULL and HUB snapshot classes skipped; as-of degree > `max_customers` skipped (reported, never linked) | shared-origin evidence (S01/S02) |
| `find_connected_entities` | `card_id`, `days` (≤ 30) | devices (from `find_shared_devices`), rare recipient/purchaser email domains and low-degree regions the card touched, other cards linked through them (bounded), `skipped_hubs` | as above | GENERIC emails, MEGA/HUB regions, NULL/HUB devices skipped; regions/emails need as-of degree ≤ 20 | connection evidence (LOW for email/region, corroboration required) |
| `find_prior_cases` | `card_id` | visible closed cases on the card, on the same customer, and on **eligible** shared devices (degree ≤ 20), each with `match_reasons`, case transactions, devices, notes text | only `close_epoch ≤ as_of` (never `opened_at`) | shared-device path only when device is eligible (§5.1) | case memory (history, not proof) |
| `detect_fraud_patterns` | `txn_id` | list of validated signals {id, name, tier, rating, fired, detail, entity ids, source}, `independent_sources`, warnings — **no probability, no verdict** | composes the tools above, all with the session `as_of` | inherited | S01 ring, S02a/b, S06 (R5), S07 burst family, S08 amount z, LOW context (S05, S09–S15) |
| `get_policy_context` | optional `signals` (from `detect_fraud_patterns`) | R1–R10, actions/routes, case-vs-report and stopping rules; which rules *may* apply to the fired signals; explicit note that this is **policy, not evidence** | none (static) | n/a | none |

Never returned: labels of arbitrary transactions, other benchmark cases, `open_epoch`-based visibility, future rows, raw GSQL, `risk_score` as a filter/ranking key (it appears only as `model_score`, context).

## Signals implemented in `detect_fraud_patterns` (validated in `docs/signal_validation.md`)

| ID | Rule | Tier / rating | Notes |
|---|---|---|---|
| S01 | Device (not NULL/HUB) with as-of `customers ≥ 3`, `new_share ≥ 0.9`, `proxy_share ≥ 0.5`; suppressed during the first 30 days of graph history | 2 / HIGH | no labels, no score; 1 ring historically |
| S02a / S02b | Device of the flagged txn with as-of degree ≤ 5 / 6–20 and another customer's confirmed-fraud case visible (`close_epoch ≤ as_of`) | 3a HIGH / 3b MEDIUM | > 20: no evidence |
| S06 | Policy R5 exactly: ≥ 3 online txns < $5 on the card within 1 h before an online txn ≥ $20 | 4 / MEDIUM (policy-defined) | 7 historical hits; not learned from history |
| S07 | Burst family: ProductCD `C`, $400–500, ≥ 1 prior same-card txn in 1 h that also matches | 4 / MEDIUM | fitted to 5 cases |
| S08 | Online amount z-score > 2 vs the card's own log-amount history (≥ 20 prior txns); `sd = sqrt(var + 0.25)` | 5 / MEDIUM | |
| S05, S09–S15 | repeat victim; new ProductCD; `id_15 = New`; anonymous/hidden proxy; New + proxy; `addr1` missing; no identity record; region away from card's modal in-person region | 6 / LOW | context only; the identity flags count as **one** independent source |

Implementation notes: signal thresholds live in `src/fraud_tools/patterns.py` next to the validated numbers; nothing new was invented. Region/email link signals S03/S04 (LOW, tiny support) are **not** turned into signals; `find_connected_entities` reports the links as context.

## Numeric sentinels (E18)
`dist1 dist2 d1 d2 d3 d5 d10 v51 v52 v79 v93 v94 v217 v258 v264 v308 = -1`, `d4 = -123`, `d15 = -84`, `addr1/addr2 = -1`, `Card.card2/card3/card5 = -1`, missing strings `""`. The wrapper converts them to `null`; no rule uses them as values.
