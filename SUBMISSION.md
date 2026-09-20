# Submission notes

The 20 answer files are in `cases/HHG-001.json` … `cases/HHG-020.json`, in the README's answer format. They were produced by the deterministic investigation pipeline
(`src/agent/`, tools in `src/fraud_tools/`) on the live `FraudInvestigation` graph, and each case was also written to the graph as an `FI_Case` vertex
(`written_to_graph: true`, `graph_case_id: CASE-<case_id>`). Actions are recommendations only; nothing is executed.

## Known limitations of this submission

1. **`fraud_probability` is `null` in all 20 files.** The README types the field as a number in 0-1 but does not say whether null is accepted, and supplies no validator. The
   only labelled outcomes are the closed cases, and they cannot yield a calibrated per-case probability for new alerts: they are selected (84% confirmed fraud, while
   the benchmark is roughly half legitimate), most alerts never became labelled cases, and the calibration gates (support, out-of-time reliability, identification of
   the alert population) fail for every evidence-strength class (`config/calibration_v1.json`, `src/calibration/`). We did not use a placeholder, a prevalence-based
   number or a loosened gate. Policy thresholds phrased as probabilities (R1, the 0.30 case-opening level, the 0.85/0.15 stopping levels) are applied through evidence
   strength and independent-source counts instead. If the scorer requires a number, this field will not score.
2. **Customer, step-up and analyst responses are simulated.** The README does not supply them, so a deterministic, seeded simulator (`src/agent/simulator.py`) answers only
   the requests the agent issues, and never reads labels. Each assumed response is stated in `evidence_requests[].assumed_response` (prefixed `[SIMULATED]`), and the
   final actions reflect that assumption. Final actions therefore depend on the simulated replies.
3. **No ground-truth answer key was supplied for the 20 cases, so no accuracy is claimed.** What we report is validation and policy consistency: every file passes the
   answer-file validator (schema, policy rules, SAR consistency, and graph checks), affected transactions and exposure are recomputed from the graph and agree, no
   transaction after the case's `opened_at` is used, no benchmark identifiers or labels appear in the graph text used for retrieval, and all actions are recommendation-only with the approval routes required by the policy.

## Reproducing

Configuration comes from a git-ignored `.env` (see `config/*.example.env`). The deterministic run needs no LLM key; the optional OpenRouter mode
(`src/agent/providers.py`) was evaluated separately on historical non-benchmark cases only and is not used for these files (`tokens` is 0).
