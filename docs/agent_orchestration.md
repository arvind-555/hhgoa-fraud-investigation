# Phase 9B: agent orchestration and MCP tool layer

Status: implemented and tested on historical, non-benchmark transactions. No LLM call is made yet (`tokens = 0`); the only free-text step (explanation) sits behind an interface.

## Layers

```
runner (trusted)  -- fixes as_of, builds InvestigationSession + ToolGateway, supplies Trigger(case_id, txn_id, trigger_type)
  |
  +-- Agent (src/agent/orchestrator.py)  state machine, deterministic evidence/uncertainty/actions
  |        \                             |
  |         ToolGateway (gateway.py)  <--+-- MCP server (mcp_server.py, stdio)  [same gateway, for an external LLM client]
  |            permissions.validate_call -> InvestigationSession (9A) -> QueryClient whitelist -> fi_* installed queries on FraudInvestigation
```

`tigergraph-mcp` (69 tools) is not mounted: our wrapper is the only MCP surface (spec sections 11 and 12).

## MCP tools (exactly eight, read-only, ids only)

`get_transaction_context(txn_id)`, `get_customer_history(customer_id, lookback_days)`, `get_card_history(card_id, hours, max_rows)`, `find_shared_devices(card_id, days)`,
`find_connected_entities(card_id, days)`, `find_prior_cases(card_id)`, `detect_fraud_patterns(txn_id)`, `get_policy_context(signals)`.
Not exposed: GSQL, schema, loading, `run_installed_query`, `write_case` (the case write is an orchestrator-internal state), any as_of/epoch/filter/order/risk-score parameter.

As-of enforcement: `python -m agent.mcp_server --as-of-epoch N` (runner-supplied process argument). A model-supplied `as_of` is dropped by the tool schema and never used;
`permissions.validate_call` rejects forbidden/unknown arguments for direct callers. Results are re-checked by `assert_visible`, then sanitised.

## What the gateway removes or changes before the agent sees a result
`as_of`, `max_epoch_seen`, `latency_ms`, `risk_score`, `snap_*`, `n_cards` are dropped; `model_score` becomes `model_alert.band` (low < 0.4 <= medium < 0.7 <= high);
`seconds_before_as_of` is renamed `seconds_since_transaction`; any string containing a benchmark case id (`HHG-<n>`) raises `LeakError`.

## State machine

`TRIGGER -> INITIAL_INVESTIGATION -> EVIDENCE_SYNTHESIS -> UNCERTAINTY_ASSESSMENT -> [ADDITIONAL_EVIDENCE_REQUEST] -> NEXT_BEST_ACTION -> EXPLANATION -> CASE_WRITE -> DONE`
(illegal transitions raise `InvariantError`; per-state tool permissions in `permissions.STATE_TOOLS`).

| State | What happens |
|---|---|
| INITIAL_INVESTIGATION | `get_transaction_context`, then in parallel: customer history, card history (30 d), shared devices, connected entities, prior cases, pattern detection; then policy context |
| EVIDENCE_SYNTHESIS | fired signals -> `Evidence` objects (E1..); affected txns, exposure, connected cards (only from devices that carry shared-origin evidence), device profiles, similar prior cases |
| UNCERTAINTY_ASSESSMENT | strength none/weak/moderate/strong from independent sources; conflicts; missing information; level; **provisional** probability band (uncalibrated) |
| ADDITIONAL_EVIDENCE_REQUEST | only if uncertainty is not low; type customer_validation or step_up_auth; the responder is injected (default: `pending`, nothing invented) |
| NEXT_BEST_ACTION | `initial` actions, then `final` actions after the response (R2/R3/R4); routes from spec 8.1; nothing executed |
| EXPLANATION | template explainer over Evidence objects; citations validated; an LLM explainer can replace it through the same interface |
| CASE_WRITE | validated case record written to `data/runs/phase9b/cases/` (local staging). Graph write is not implemented |

## Evidence object and uncertainty
`agent/schema.py`: `Evidence(id, source_tool, kind, summary, rating, signal_id, tier, independent_source, direction, refs, max_epoch)` and
`Uncertainty(evidence_strength, independent_sources, conflicts, missing, level, needs_more_evidence, provisional_probability{low,point,high,calibrated:false,basis}, reasons)`.

## Deterministic invariants (checked before a case is written)
legitimate => no affected txns and no exposure; exposure = sum of affected amounts; affected txns were observed in tool results; pattern_description only for `undocumented`;
no action executed; no `BLOCK_ALL_CARDS`; no block before verification; explanation cites only existing evidence ids.

## Latency design
Per-session single-flight cache of identical graph queries; independent tool calls run in parallel; identical logical calls are memoised in the gateway.
The 9A composite (11 queries, 8-14 s per case) becomes 7 queries per case, 2.2-3.6 s.

## Known limits (carry into Phase 10)
Probability tiers are placeholders (spec B3); the customer/step-up simulator is not defined (B4); R7 not evaluable (B6); LLM and embedding choice open (B5);
case write to the graph, answer-file validator and SAR narrative are not built; connected-card transactions are not included in `affected_txn_ids`
(only the flagged card's transactions on the evidence device are known to the tools).

## B4: deterministic evidence simulator (policy b4-v1) and connected-card exposure

**Simulator** (`src/agent/simulator.py`, policy b5-v1, supersedes the hash-seeded b4-v1). NO randomness and NO hash: a response is a pure function of
(scenario, request type, trigger type). The task supplies no replies, so the default scenario `no_reply` assumes "no reply within 24 hours" (absence of evidence, not
testimony; policy R4 applies). Explicit what-if scenarios `cardholder_confirms` / `cardholder_denies` exist for tests and demonstrations and are never used by the benchmark run.
A customer REPORT is immutable trigger evidence: the simulator refuses to have that customer confirm the transaction. It has no graph client, data, labels, closed cases, clock or risk score
(`tests/test_simulator_exposure.py` checks its only import is `re`). It refuses unissued requests, other cases, type mismatches and analyst_info.
What each response represents: `no_response` = no evidence (R4); `verified_legitimate` = what-if: cardholder confirms (R3); `denied_or_unrecognized` = what-if: cardholder does not
recognise it (R2); `passed` / `failed` = what-if: authentication outcome. All text is prefixed `[SIMULATED]`; simulated evidence is a separate frozen `Evidence(simulated=True)` appended after
graph evidence, and case output flags it (`evidence[].simulated`, `simulated_evidence_ids`, `evidence_requests[].simulated`). `no_response` adds no evidence.

**Decision matrix** (`src/agent/actions.py`, the module docstring is the table). Classes: A strong graph evidence; T+ customer report + one validated source; T customer report only;
B one validated source; C weak / none. Verification states: D positive, E negative, F no reply. Verdicts come only from evidence class + response + policy (`decide_verdict`);
`policy_gaps` makes the orchestrator raise if R6 / R9 / 3a required actions (CREATE_CASE, FILE_REPORT, MONITOR_CONNECTED_CARDS) are missing. Insufficient evidence yields `uncertain`.
Evidence independence: pattern-defining characteristics (S10/S11/S12 inside an S01 ring) and weak context signals are recorded but never counted as independent evidence; a customer
report counts as one separate (unverified) source.

**Exposure** (`Agent._expand_exposure`). When S01 or S02a/b fires, for each other card on the evidence device(s) (from `find_shared_devices` neighbours, at most 40 cards) call
`get_card_history` and add its transactions that (a) are on the evidence device, (b) are within 30 days before the flagged transaction and not after as_of (guaranteed by the tool and re-checked),
and (c) do not belong to a visible closed case. `exposure_usd` = sum of |amount| over `affected_txn_ids` (flagged card's evidence-device transactions + those). `exposure_scope` reports
own/other counts, cards expanded, cards not expanded and `complete`; hub-sized rings that exceed the 25 listed neighbours are declared incomplete, not silently truncated.

## B3 calibration and production case writing (milestone 9C)

**Calibration** (`src/calibration/`, artifact `config/calibration_v1.json`, report `data/runs/calibration/report.json`). Evidence strength (none/weak/moderate/strong) is recomputed point-in-time at each
closed case's flagged transaction (`frame.py`, verified 40/40 against the live agent) and mapped to P(confirmed fraud) with empirical-Bayes bucket rates. Labels: `closed_cases_history.csv` only, gated to
`close_epoch <= 2016-11-01` (the label horizon; the runtime refuses calibrated=true for an as_of before it). Out-of-time split: train cases closed <= 2016-09-15, test cases flagged after it.
A bucket is calibrated=true only if G1 (horizon), G2 (support), G3 (out-of-time reliability) and G4 (identification: Manski bounds on P(fraud | alert) narrower than 0.20) all pass.
Result on the available data: **no bucket passes** (G4 fails everywhere: only ~25% of risk>=0.5 transactions ever became labelled cases, unlabeled != legitimate), so `fraud_probability` stays a
placeholder with `probability_calibrated=false` and the failed gate names are recorded. The agent receives only probability, flag, method and gate names, never counts or rates.

**Case write** (`case_validator.py`, `case_writer.py`, `graphio.py`). Validator invariants S1-S8 (structure, enums, action/route rules, legitimate consistency, what_changed, calibrated flag, citations,
simulated-evidence marking, leaks) and G1-G4 (ids exist in the graph, epochs <= as_of, exposure recomputed from graph amounts, other-card txns on a cited device, prior cases visible by close_epoch).
Writer: `graph_case_id = CASE-<source_case_id>`; validate -> compare content hash -> upsert -> read back. Same content = no write (revision kept); changed content = revision+1 with this case's edges replaced;
a different as_of for an existing case is refused. Written: one `FI_Case` vertex (as_of_epoch, provenance and content hash inside evidence_json, simulated evidence marked) and edges
CASE_ON_CARD, CASE_TXN (role), CASE_CONNECTED_TO (via device), CASE_CITES_DEVICE, SIMILAR_CASE -> ClosedCase. GraphIO refuses any other vertex/edge type; deletes are limited to `CASE-TEST-` vertices.
Not written yet: SAR narrative (`sar_json.status = narrative_not_generated`), TextChunk/DESCRIBES (GraphRAG).

## Milestone 9D: null probability, SAR, answer file, LLM, GraphRAG, R7

**Probability (decision).** The calibration experiment is a failed validation gate, not a missing feature. `fraud_probability = null`, `probability_calibrated = false`, `probability_source = "unavailable"`.
No placeholder, no assumed prevalence. The code path that would return a number exists only if a calibration artifact ever passes every gate (never true on the current labels).
The FI_Case vertex stores -1.0 (the E18 "missing" convention) and `evidence_json.probability.value = null`. The validator rejects any stated but uncalibrated probability (S5).
Policy thresholds phrased as probabilities (R1 "< 0.70", 3a "0.30", stopping "0.85/0.15") are applied through evidence classes: R1 = a single independent source that is not strong; a case opens when
evidence is at least moderate, evidence is requested, or the customer disputes. The explanation always separates: **evidence strength** (what validated evidence exists), **investigation uncertainty**
(conflicts, missing data, pending evidence) and **calibrated probability** ("not available" with the failed-gate reasons).

**SAR** (`sar.py`): deterministic facts -> narrative (who/what/when/where/how/why, 6-12 sentences), `file` iff FILE_REPORT is in the final actions, subjects, total = exposure, first/last dates; blank otherwise.
`check_narrative` validates any narrative (template or LLM): known ids and amounts only, no probability, simulated response labelled.

**Answer file** (`answer_file.py`): `assemble` builds exactly the README fields; `validate_answer` checks schema, enums, rules and (with GraphIO) ids, epochs <= as_of, exposure from graph amounts over deduplicated ids,
written_to_graph resolution, measured tool_calls/tokens/latency. `fraud_probability = null` is a WARNING (the README types the field as a number) - a project decision to confirm before submission.

**LLM (B5)** (`llm.py`): wording only (explanation, SAR narrative). Sanitised structured facts in, validated text out, deterministic fallback on any failure; stdlib HTTPS client, key from ANTHROPIC_API_KEY, model
HHG_LLM_MODEL (default claude-haiku-4-5-20251001), temperature 0, per-case budget 4,000 tokens (estimate-before, charge-after), response cache for reproducible reruns. No live call has been made (no key in this environment).

**GraphRAG** (`src/rag/`): 5,594 TextChunk vertices (5,565 closed-case, 21 policy, 6 pattern, 2 format) + 5,565 DESCRIBES edges loaded into FraudInvestigation; installed query `fi_text_chunks(as_of, doc_type, channel,
max_chunks)` returns only chunks with valid_from_epoch <= as_of (closed-case chunks: close_epoch via DESCRIBES). Retrieval = structured seeds (`linked`) + BM25 (`precedent`); no vectors/embeddings yet
(embedding model/dimension undecided). New agent tools: `find_similar_cases(txn_id, k)` and `retrieve_policy(rule_ids)` (10 tools total). Policy text consulted for every cited rule becomes `document` evidence.

**R7**: assessed and documented in `docs/r7_assessment.md` - not evaluable (no merchant; the recurring-amount proxy points the wrong way and fails its gate).

## Milestone 9E: LLM-orchestrated investigation (agentic layer)

**Finding.** Before this change the model only reworded deterministic outputs: the orchestrator ran a fixed tool pipeline and a fixed evidence-request rule, so the README's "investigates ... and knows when it
needs more evidence" was met by code, not by the model. `agent/investigator.py` adds the smallest change that makes the model the orchestrator while the deterministic layer stays the authority.

**What the model decides** (`LLMInvestigator`, Anthropic tool use): which of the 10 permitted tools to call, in what order, with which (id-only, bounded) arguments; when the investigation is sufficient
(`finish_investigation` with missing evidence and a rationale); whether to request additional evidence (`request_evidence` customer_validation / step_up_auth, or `no_further_evidence`); the wording of the
explanation and SAR (validated). It sees a compact view of each result, no as_of, no GSQL, no labels, no bank score (not even the band), no benchmark ids (`guard_payload` blocks every outbound message).

**What stays deterministic and authoritative:** every call executes through `ToolGateway` (whitelist, argument validation, runner-injected as_of, leak check, sanitising); refused calls are echoed back
without their arguments; evidence completion (the signal engine, and card history / shared devices / prior cases whenever a signal fired) is forced if the model skipped it and recorded as `forced`; the policy decides
which verification is REQUIRED (the model may ask for more, never fewer; the type must be allowed, a customer report always uses customer validation); actions, routes, exposure, SAR, invariants, validator and graph
writes are unchanged. Any model failure (no key, network, budget, protocol) falls back to the fixed pipeline (`fallback_used`).

**Measured per case** (`record["agentic"]`, internal only, never in the answer file): tool order with source (llm / forced), refused / duplicate / non-contributing calls, coverage of the required evidence by the model
itself vs after forcing, tokens by phase, model latency, request decision source (policy / llm / llm+policy / policy_overrode_llm), fallback. `src/agent/run_agentic_eval.py` aggregates them against the
deterministic baseline and checks that the authoritative case is identical.

## Provider boundary: OpenRouter free model (agent/providers.py)

`OpenRouterHTTP` implements the same `complete()` / `chat()` interface as `AnthropicHTTP` and translates at the edge (system prompt, tool definitions, tool_use <-> tool_calls, tool_result <-> role "tool",
tool_choice "any" -> "required" with one reminder retry). Nothing above the boundary changed: investigator, orchestrator, gateway, permissions, validators, SAR/answer file are untouched, and
`guard_payload` + the token budget run before any provider call, so OpenRouter receives exactly what Anthropic would.
* Free models only: the id must end with `:free`; no paid model, no fallback list, no credits. Default `nvidia/nemotron-3-super-120b-a12b:free` (listed at price 0/0, 262k context, advertises tools,
  tool_choice and structured outputs; verified with a toy multi-turn tool-call probe: valid schema-conforming arguments, called the next tool after a tool result; `tool_choice: "required"` was NOT honoured in that probe).
  `google/gemma-4-31b-it:free` and `qwen/qwen3.8-27b:free` also advertise tools but returned HTTP 429 during selection, so they are unverified.
* Configuration: HHG_LLM_PROVIDER, OPENROUTER_API_KEY, OPENROUTER_BASE_URL, HHG_LLM_MODEL, HHG_LLM_TIMEOUT_S, optional attribution headers (config/llm.example.env). Process env first, then .env; only these names are read.
* The key is never printed, logged, or placed in errors/repr; provider error bodies are never echoed; prompts and raw responses are not logged (only the local, git-ignored response cache keeps replies).
* The model id RETURNED by OpenRouter is recorded per call (`models_seen`) and in every result's `model`; `run_agentic_eval.py` writes `models_returned` per case and per mode.
