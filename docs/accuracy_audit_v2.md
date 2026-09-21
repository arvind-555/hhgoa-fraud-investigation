# Accuracy Audit v2: after the decision-layer fixes

Follow-up to `docs/accuracy_audit.md` (RED). The evidence and temporal architecture is unchanged; the decision layer was rebuilt so that a verdict follows from evidence, never from a simulated reply.
Same tags as v1: **[F]** documented fact / computed from raw data at `as_of`, **[D]** deterministic evidence produced by the system, **[I]** investigator inference, **[A]** assumption, **[U]** unknowable.
There is still no answer key, so nothing here claims accuracy; it claims evidence-grounded, policy-consistent decisions.

## 1. What was wrong

1. Verdicts came from a hash-seeded simulator (`SHA-256(seed|case_id|request_type)`, 40/40/20 and 45/35/20 splits): 19 of 20 verdicts depended on the simulated reply.
2. A customer *report* ("I never made this purchase") could be "confirmed legitimate" by the simulator and closed with `CLOSE_NO_FRAUD` (HHG-003, 009, 016).
3. HHG-014's strong-ring branch collapsed to `ESCALATE_TO_ANALYST` after a simulated step-up pass, dropping R6/R9/3a actions, and the SAR "no report" reason cited the final actions (circular).
4. 3a (a request opens a case) was missing in 9 initial-action lists; R4 was unreachable; the recommended verification and the request actually made disagreed for 6 customer reports.
5. "2 independent sources" counted identity flags that define the S01 rule itself.

## 2. What changed (no hidden labels, no benchmark answers, no future data, no probabilities, no ML, LLM still not an authority)

| Area | Change |
|---|---|
| Simulator (`simulator.py`, b5-v1) | Removed the hash draw and all randomness. One default assumption, `no_reply` = "no reply within 24 hours": absence of evidence, not testimony. Explicit what-if scenarios (`cardholder_confirms` / `cardholder_denies`) exist for tests and demos only; the benchmark never uses them. A customer report is immutable: the simulator refuses to have that customer confirm. Every response is `[SIMULATED]` and documented (`MEANING`). |
| Decision matrix (`actions.py`) | Classes A strong, T+ report + one validated source, T report only, B one validated source, C weak, and verification states D positive / E negative / F no reply; each row defines verdict, actions, route, and whether CREATE_CASE / FILE_REPORT / MONITOR_CONNECTED_CARDS are required. `decide_verdict` derives the verdict from class + response + policy. `policy_gaps` makes the orchestrator raise if a required action is dropped. |
| Strong ring | Final actions keep CREATE_CASE, FILE_REPORT (L2), MONITOR_CONNECTED_CARDS and add ESCALATE; a passed step-up or confirmation of the flagged customer no longer removes them. |
| SAR (`sar.py`) | The decision to file comes from the action engine (policy 3a conditions); the *reason* is explained from case facts (verdict, evidence, exposure, shared origin), never from the selected actions. |
| Independence (`orchestrator._assess`) | Pattern-defining characteristics (S10/S11/S12 inside an S01 ring) and weak context are recorded (`pattern_defining`, `corroborating`) but never counted; a customer report is one separate, unverified source. HHG-014: 2 -> 1 independent source. |
| R4 / 3a / request type | A simulated no-response now applies R4 (MONITOR_CARD, DECLINE_TRANSACTION, escalate above $500); evidence requests open a case; the request type follows the recommended verification (STEP_UP_AUTH -> step_up_auth, VERIFY_WITH_CUSTOMER -> customer_validation). |
| Text | "customer response not available yet" is no longer stated after the request; the explanation separates validated sources, pattern-defining characteristics and the customer's statement. |

## 3. Before / after decision logic

Before: `verdict = f(hash(seed, case_id, request_type))` for every alert with weak evidence; `A + response` collapsed to a single escalation; `no_response` was "pending".
After: `verdict = decide_verdict(evidence class, response, policy actions)`; the same evidence always gives the same decision; insufficient evidence is `uncertain`.

## 4. Investigation of the three flagged signals (before any change)

| Case | Finding | Verdict on the concern |
|---|---|---|
| **HHG-006** | The bank's own closed-case history labels this burst family `undocumented` (5 closed cases, 4 transactions, ~$1,900, all `CREATE_CASE|BLOCK_CARD|FILE_REPORT`, report filed) **[F]**. README pattern 2 also fits, but the labelled precedent is `undocumented`. | **Not a genuine mislabel.** The classification and the action set are precedent-consistent. The unused channel-shift / device-alternation features are context; S07 already fires. No change. |
| **HHG-011** | A card with 10,306 prior online transactions is in the top 0.01% by volume, but in the labelled history high velocity is *anti*-informative: fraud share 0.036 / 0.044 / 0.040 for 0-5 / 5-20 / 20-50 prior-24h transactions and **0.023 (lift 0.61) at 50+** **[F]**. | **Not genuine.** A velocity signal would contradict the data. Forcing one would be wrong. No change. |
| **HHG-015** | Under the **production** definition S08 does not fire (z 1.93 with the variance floor; 2.10 with a 0.1 floor). The v2 draft claimed the floor suppresses as-informative transactions (579 excluded at 25.4% vs 25.2%). **That did not survive verification** (section 4a). | **Not confirmed. No code change.** |

### 4a. S08 variance-floor verification (retraction of the earlier finding)

Traced: `patterns.evaluate` (S08) -> `amount_z = (ln(1+amt) - mu) / sqrt(var + Z_VAR_FLOOR)` with `Z_VAR_FLOOR = 0.25`, threshold `z > 2`, `n_base >= 20`, online only; `mu`/`var` come from the
`fi_card_history` accumulators, which cover **all channels** of the card, strictly before the flagged transaction and `<= as_of` (point-in-time safe). One S08 hit is one tier-5 independent source
(`amount_history`), so evidence strength becomes `moderate` (`orchestrator._assess`), i.e. class B (or T+ for a customer report) in the decision matrix.

Why the earlier figure was wrong: it was computed with an **online-only** card baseline, which is not what production uses. Replicating the production baseline exactly reproduces the documented S08
(251 flagged, 22.3% fraud share) and gives:

| Rule (online, >= 20 prior card txns, Aug 1 - Nov 1; base fraud share 11.25%) | flagged | fraud share | lift | cleared |
|---|---|---|---|---|
| current S08 (floor 0.25) | 251 | 22.3% (95% CI 17.6-27.9) | 1.98 | 2 |
| floor 0.10 | 452 | 20.4% | 1.81 | 4 |
| floor 0.05 | 572 | 20.5% (17.4-24.0) | 1.82 | 4 |
| floor 0.01 | 670 | 19.3% | 1.71 | 5 |
| **added by floor 0.05** (the previously excluded population) | 321 | **19.0%** (15.1-23.7) | **1.69** | 2 |
| **added by floor 0.01** | 419 | **17.4%** | **1.55** | 3 |

- The excluded population is **less** informative than S08 (17-19% vs 22.3%), not equal. Relaxing the floor would dilute a MEDIUM signal (lift ~2.0) into lift ~1.7-1.8, between the project's LOW (<= 1.5) and MEDIUM (>= 2) tiers.
- Point-in-time safety: baselines use only earlier transactions; the window ends Nov 1, before every benchmark `as_of`; the labels (confirmed-fraud closed cases) are used **only to evaluate** the signal offline and are never read at decision time. No benchmark label, no future data, no `risk_score`, no randomness is involved.
- Conclusion: the floor is not "incorrectly suppressing MEDIUM evidence". The correct evidence-quality outcome is **no change to S08**. A milder signal would be a *new LOW-tier signal*, not a correction of S08, and is out of scope.

| Case | S08 before | S08 if the floor were relaxed | Strength | Verdict / actions (submitted) | What-if (for information only) |
|---|---|---|---|---|---|
| HHG-004 | not fired (z 1.72) | fires only at floor <= 0.01 (z 2.06); **not** at 0.05 (1.99) | weak | uncertain/open; MONITOR_CARD, DECLINE[L1], CREATE_CASE | would become T+ -> fraud/BLOCK_CARD on a *marginal* z of 2.06: exactly the outcome that must not drive a change |
| HHG-015 | not fired (z 1.93) | fires at floor <= 0.1 (z 2.10) | weak | uncertain/escalated; MONITOR_CARD, DECLINE[L1], ESCALATE, CREATE_CASE | class B, same verdict and same final actions (already escalated under R8 because of the conflicting alert) |

Both cases therefore stay **uncertain**, unchanged.

## 5. Case-by-case changes (submitted final decision, before -> after)

| Case | Verdict / status | Final actions before | Final actions after | SAR |
|---|---|---|---|---|
| HHG-001 | legitimate/closed -> uncertain/open | CLOSE_NO_FRAUD | MONITOR_CARD, DECLINE_TRANSACTION[L1], CREATE_CASE | no |
| HHG-002 | legitimate/closed -> uncertain/open | ALLOW, MONITOR_CARD | MONITOR_CARD, DECLINE[L1], CREATE_CASE | no |
| HHG-003 | legitimate/closed -> uncertain/open | CLOSE_NO_FRAUD | MONITOR_CARD, DECLINE[L1], CREATE_CASE | no |
| HHG-004 | uncertain/open (same) | STEP_UP_AUTH, MONITOR_CARD, CREATE_CASE | MONITOR_CARD, DECLINE[L1], CREATE_CASE | no |
| HHG-005 | legitimate/closed -> uncertain/open | ALLOW, MONITOR_CARD | MONITOR_CARD, DECLINE[L1], CREATE_CASE | no |
| HHG-006 | fraud/closed_fraud (same) | BLOCK_CARD[L1], CREATE_CASE, FILE_REPORT[L2] | same, now from evidence (report + S07), no request | yes |
| HHG-007 | uncertain/open (same) | VERIFY, MONITOR_CARD | MONITOR_CARD, DECLINE[L1], CREATE_CASE | no |
| HHG-008 | fraud/closed_fraud -> uncertain/open | BLOCK_CARD[L1], CREATE_CASE | MONITOR_CARD, DECLINE[L1], CREATE_CASE | no |
| HHG-009 | legitimate/closed -> uncertain/open | CLOSE_NO_FRAUD | MONITOR_CARD, DECLINE[L1], CREATE_CASE | no |
| HHG-010 | uncertain/escalated (same) | DECLINE[L1], CREATE_CASE, VERIFY, MONITOR, ESCALATE | MONITOR_CARD, DECLINE[L1], ESCALATE, CREATE_CASE | no |
| HHG-011 | uncertain/open (same) | STEP_UP_AUTH, MONITOR_CARD, CREATE_CASE | MONITOR_CARD, DECLINE[L1], CREATE_CASE | no |
| HHG-012 | fraud/closed_fraud -> uncertain/open | BLOCK_CARD[L1], CREATE_CASE | MONITOR_CARD, DECLINE[L1], CREATE_CASE | no |
| HHG-013 | uncertain/open (same) | DECLINE[L1], CREATE_CASE, VERIFY, MONITOR | MONITOR_CARD, DECLINE[L1], CREATE_CASE | no |
| HHG-014 | fraud/escalated (same) | ESCALATE_TO_ANALYST | CREATE_CASE, ESCALATE, FILE_REPORT[L2], MONITOR_CONNECTED_CARDS, VERIFY, MONITOR_CARD, DECLINE[L1] | **no -> yes** |
| HHG-015 | legitimate/closed -> uncertain/escalated | ALLOW, MONITOR_CARD | MONITOR_CARD, DECLINE[L1], ESCALATE, CREATE_CASE | no |
| HHG-016 | legitimate/closed -> uncertain/open | CLOSE_NO_FRAUD | MONITOR_CARD, DECLINE[L1], CREATE_CASE | no |
| HHG-017 | uncertain/open (same) | STEP_UP_AUTH, MONITOR_CARD | MONITOR_CARD, DECLINE[L1], CREATE_CASE | no |
| HHG-018 | fraud/closed_fraud -> uncertain/open | BLOCK_CARD[L1], CREATE_CASE | MONITOR_CARD, DECLINE[L1], CREATE_CASE | no |
| HHG-019 | legitimate/closed -> uncertain/open | ALLOW, MONITOR_CARD | MONITOR_CARD, DECLINE[L1], CREATE_CASE | no |
| HHG-020 | legitimate/closed -> uncertain/open | ALLOW, MONITOR_CARD | MONITOR_CARD, DECLINE[L1], CREATE_CASE | no |

Verdicts: before 9 legitimate / 5 fraud / 6 uncertain; after **2 fraud / 18 uncertain / 0 legitimate**.

## 6. Evidence supporting each changed decision

- **HHG-014 (fraud, escalated, SAR)** **[D]**: shared-origin ring (S01): one device profile on 44 customers, 100% new-device, 100% proxied, 100% Product C, the only one of 319 comparable profiles meeting both thresholds **[F]**; 26 transactions on 20 cards in the last 30 days, $3,778.14; 4 customers with visible confirmed fraud on the profile. R6 requires CREATE_CASE + FILE_REPORT + MONITOR_CONNECTED_CARDS; R9 requires the same plus escalation. Nothing about the flagged customer's own reply can remove them, and none was assumed anyway.
- **HHG-006 (fraud)** **[D]/[F]**: the customer's report is trigger evidence; the S07 burst (4 online Product C purchases of $457-488 within ~30 minutes on an otherwise in-person card) is a validated independent source; the labelled history for this family shows the same action set. Two independent sources, so R2 applies without a request; FILE_REPORT because exposure is $1,906.07 (> $1,000).
- **All 18 uncertain cases**: the evidence is weak, contradictory or single-source, and the only thing that could resolve it (a customer or step-up reply) is assumed absent. Verdict uncertain is the correct answer under the task's own rule "uncertain is valid"; the actions are R1 (verification recommended first), 3a (a case) and R4 (no reply within 24 h: monitor, decline pending authorisations, escalate above $500: HHG-010 and HHG-015).

## 7. Second adversarial pass (same attacks, re-run on the new outputs)

| Check | Result |
|---|---|
| Verdict set by a simulated reply | **0** (the default simulator returns no evidence; no verdict, status or closure depends on a random or hashed draw; a test forbids `hashlib` / `random` in the decision path) |
| Customer report contradicted or closed legitimate | **0** (all 8 reports stay open or are corroborated; the simulator raises on a contradicting scenario) |
| Strong ring keeps R6/R9/3a | **Yes** (policy guard would raise otherwise) |
| SAR reason circular | **No**; explained from verdict, evidence and 3a conditions; SAR file = FILE_REPORT for all 20 |
| Evidence requested without CREATE_CASE in the initial actions | **0** (was 9) |
| Verification recommended != request made | **0** (was 6) |
| No-reply cases missing R4 actions | **0** |
| Temporal / benchmark leakage, policy violations, graph-write failures | **0 / 0 / 0 / 0** on the full 20-case run |
| Simulated evidence items in the 20 files | **0**: no customer testimony is invented |
| Non-null probabilities | **0** (still unsupported by the data) |

## 8. Remaining uncertain cases and known limitations

**Remaining MEDIUM concerns (none HIGH):**
1. **R4 is an assumption-driven action set.** Assuming "no reply within 24 h" applies MONITOR_CARD + DECLINE_TRANSACTION[L1] to 17 cases, some of whose own evidence looks benign (e.g. HHG-007, 019, 020). The rule is policy text, the decline only concerns still-pending authorisations, and the alternative assumption ("reply still awaited") would keep the initial actions; the choice is disclosed, not hidden.
2. **No case is concluded legitimate.** Without positive verification the system says `uncertain`. This is defensible, but it under-decides versus a key that labels many cases legitimate.
3. **Customer reports with weak evidence are not blocked.** An unverified report is one signal, so R1 (verify first) applies; R2 (block) applies once corroborated or verified. A different reading of R1/R2 would recommend BLOCK_CARD earlier.
4. **S08 near-threshold amount anomalies** (HHG-004, HHG-015): a residual false-negative possibility, **not correctable** without diluting S08 (section 4a). It is mitigated: neither case is allowed or closed; both are open and monitored (HHG-015 escalated).
5. **S01 rests on one seeded ring** and has a 3-customer floor; it is rated HIGH by design, not by independent labelled precision.
6. **Exposure for report-only cases is the flagged transaction only**; later same-card transactions are not examined.
7. **LLM layer**: not the authority, untested on the benchmark, fell back on most historical runs. The submitted results are deterministic (`tokens: 0`).

**Correction:** the earlier draft of this document listed the S08 floor as a genuine, fixable false negative. Verification against the production baseline retracts that finding (section 4a).

**Still true and disclosed:** `fraud_probability` is null; R7 cannot be evaluated (no merchant field); simulated responses are assumptions; no answer key, so no accuracy claim.

## 9. Second audit rating

**YELLOW.** The three HIGH findings of v1 are resolved (verdicts no longer follow a simulator; customer reports are immutable; the strong-ring branch keeps its required actions and the SAR is explained from case facts). Remaining weaknesses are MEDIUM, explainable and disclosed above; none changes a verdict through an unsupported mechanism.

## 10. Final adversarial review of all 20 cases (submitted outputs)

Independent evidence reconstructed from raw data at each `as_of` (as in v1); each row states the residual concern only. Severity: NONE / LOW / MEDIUM / HIGH / CRITICAL.

| Case | Verdict / final actions | Residual concern | Sev. |
|---|---|---|---|
| HHG-001 | uncertain; MONITOR, DECLINE[L1], CASE | typical in-person purchase, but 4 prior confirmed frauds on the customer: monitoring is appropriate; decline is R4 text | LOW |
| HHG-002 | uncertain; MONITOR, DECLINE[L1], CASE | one MEDIUM signal (amount 6.2x median) + score 0.79 + prior fraud; not cleared on an assumed reply | LOW |
| HHG-003 | uncertain; MONITOR, DECLINE[L1], CASE | report-only; behaviour typical; R1-before-R2 reading (cross-case MEDIUM) | LOW |
| HHG-004 | uncertain; same | report + amount 4.6x median, first use of this device; S08 near threshold (z 1.72), not correctable | LOW |
| HHG-005 | uncertain; same | ordinary amount, common device profile; nothing suspicious | LOW |
| HHG-006 | **fraud**; BLOCK[L1], CASE, REPORT[L2] | report + validated burst, precedent-consistent; status `closed_fraud` while L1/L2 approvals are pending | LOW |
| HHG-007 | uncertain; same | score 0.87 contradicted by normal behaviour; 18 prior frauds on the customer | LOW |
| HHG-008 | uncertain; same | report-only; typical amount, known device | LOW |
| HHG-009 | uncertain; same | report-only; no evidence at all | LOW |
| HHG-010 | uncertain/escalated; MONITOR, DECLINE[L1], ESCALATE, CASE | 18x amount + new device; verdict uncertain rests on the assumed absence of a reply | LOW |
| HHG-011 | uncertain; same | 10,306-transaction card is unusual, but velocity is anti-informative in the labelled history: no signal | LOW |
| HHG-012 | uncertain; same | ordinary in-person purchase | LOW |
| HHG-013 | uncertain; same | ordinary amount; unspecific device bucket | LOW |
| HHG-014 | **fraud/escalated**; CASE, ESCALATE, REPORT[L2], MONITOR_CONNECTED, VERIFY, MONITOR, DECLINE[L1] | S01 rests on one seeded ring with no independent labelled precision; all 26 recent transactions are treated as one episode on device sharing alone (81% of the early burst was never in a closed case) | MEDIUM |
| HHG-015 | uncertain/escalated; same + ESCALATE | largest purchase the card has made, new device: S08 not fired at z 1.93; documented limitation; escalated, not allowed | LOW |
| HHG-016 | uncertain; same | report-only; ordinary amount | LOW |
| HHG-017 | uncertain; same | hidden proxy only; device previously used by the card | LOW |
| HHG-018 | uncertain; same | report-only; home-region purchase on a very active card | LOW |
| HHG-019 | uncertain; same | score 0.90 with nothing behind it; recorded as a conflict | LOW |
| HHG-020 | uncertain; same | thin online history: S08 cannot be evaluated (stated) | LOW |

Counts: HIGH 0, MEDIUM 1, LOW 19. Cross-case MEDIUM concerns are unchanged (section 8): assumed-no-reply R4 actions, no legitimate verdicts, R1-before-R2 reading for reports, S01 single-ring validation, exposure for report-only cases, untested LLM layer.
Regression checks on the final outputs: no verdict or status set by a simulated reply, no report contradicted, R6/R9/3a preserved for the ring, SAR reasons non-circular, 0 temporal / benchmark leakage, 0 policy violations, 0 probabilities.

**Final assessment: YELLOW** (unchanged): no HIGH findings, remaining weaknesses are MEDIUM, explainable and disclosed.
