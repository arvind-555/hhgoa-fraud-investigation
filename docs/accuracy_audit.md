# 20-Case Accuracy Audit

Adversarial audit of the investigation agent, written from a hostile-judge / senior-investigator position. **No code was changed.** No verdict, action or case output was edited to fit the audit.

## How to read this document

Every statement is tagged with its epistemic status:

| Tag | Meaning |
|---|---|
| **[F]** | Documented fact: stated in the README / policy / dataset, or a number computed from the raw files at the case's `as_of`. |
| **[D]** | Authoritative deterministic evidence: produced by the agent's tools and rules (`src/fraud_tools`, `src/agent`). Authoritative for *this system*, not necessarily correct. |
| **[I]** | Reasonable investigator inference from [F]/[D]. Could be wrong. |
| **[A]** | Unsupported assumption: nothing in the supplied data backs it. |
| **[U]** | Genuinely unknowable from the dataset (no answer key, no customer replies, no merchant field). |

**Method.** Each case was rebuilt independently from the raw `transactions.csv`, `identity.csv` and `closed_cases_history.csv`, using only rows with `epoch <= as_of` (closed-case labels only when `close_ep <= as_of`). Nothing came from the agent's tools, the benchmark outputs, or any hidden label. `risk_score` was used only as the trigger input it is, never as ground truth. The audit compared that reconstruction with the submitted `cases/*.json` and the stored agent records. The verdict-sensitivity numbers (Cross-case finding 1) come from re-evaluating the agent's own pure decision functions (`actions.initial_actions/final_actions` and the verdict rule in `orchestrator._next_best_action`) on each stored record under each possible simulated reply; that is analysis only, with no graph writes.

**Hard limit.** There is no answer key for the 20 cases, so this audit cannot say which verdicts are *correct*. It can only say which are *defensible from the evidence available at `as_of`* and which are not. **[U]**

---

## Headline

The **evidence layer** (signals, temporal safety, provenance) is solid and honest. The **decision layer** is where a hostile judge wins:

1. **19 of the 20 verdicts are set by a hash-seeded simulator, not by evidence.** With no simulated reply (`pending`) the evidence supports `uncertain` for 19 cases and `fraud` for exactly one (HHG-014). The simulator draws the reply from `SHA-256(seed | case_id | request_type)` with fixed 40/40/20 and 45/35/20 splits, independent of the case. **[D]**
2. **Three customer *reports* were closed as legitimate because the simulator had the customer "confirm the purchase"**, contradicting the report that opened the case (HHG-003, 009, 016). The action is `CLOSE_NO_FRAUD` on the `auto` route.
3. **HHG-014 (the showcase) ends with `ESCALATE_TO_ANALYST` only.** After a simulated step-up "pass" for the flagged customer, the agent drops `CREATE_CASE`, `FILE_REPORT` and `MONITOR_CONNECTED_CARDS`, which policy R6 / R9 / 3a call for, while keeping verdict `fraud` and $3,778.14 exposure across 19 other cards. The SAR "no report" reason is circular (it cites the final actions as the reason).
4. **Policy 3a ("open a case whenever you request evidence") is not followed in 9 initial-action lists**, and R4 ("no reply within 24 h → MONITOR_CARD + DECLINE_TRANSACTION") is never applied because a simulated `no_response` is mapped to `pending`.

Strongest positives: zero temporal violations across all 39 cited transaction ids; every closed case closed at least 5 days before the first benchmark `as_of`; the ring is a genuine statistical outlier; approval routes are computed correctly.

---

## Phase 1: per-case audit

Legend for **Evidence**: S-codes are the agent's signals; ratings in brackets are the agent's own. "Independent" facts are from my reconstruction, not from the agent. Severity: NONE / LOW / MEDIUM / HIGH / CRITICAL.

| Case | Trigger | Strongest evidence | Weakest / noisiest evidence | Detected pattern | Additional evidence | Final action (route) | Verdict | Main concern | Judge attack | Severity |
|---|---|---|---|---|---|---|---|---|---|---|
| **HHG-001** | risk_score 0.61, in-person $77.07, region 444. Interpreted correctly. | S05 repeat victim [LOW]: the customer has 4 visible confirmed-fraud cases **[D]**. Independent: amount typical (z −0.11, median $83.72), 358 prior txns **[F]**. | Model score; card visits many regions (40, modal share 0.10) so region novelty means little. | none | customer_validation → *simulated* "made it" | CLOSE_NO_FRAUD (auto) | legitimate / closed | Closed a customer with 4 prior confirmed frauds on a hash-drawn reply; no CREATE_CASE although evidence was requested (3a). | "A random number closed a repeat-victim alert." | MEDIUM |
| **HHG-002** | risk_score 0.79, online $292.36, Product C, no identity record. | S08 amount anomaly [MEDIUM]: z 2.92, 6.2× the online median ($47.29), largest amount on the card **[D]**. | S13/S14 (no identity: lift 0.51), S05. | card_not_present_fraud | step_up_auth → *simulated* pass | ALLOW_TRANSACTION, MONITOR_CARD (auto) | legitimate / closed | One MEDIUM signal + 0.79 score + prior confirmed fraud on the customer, cleared by a random pass. Pattern label rests on a single signal. Under a simulated fail the verdict is only `uncertain`. | "Your verdict flips on a coin; your evidence never says fraud." | MEDIUM |
| **HHG-003** | customer_report, in-person $49, region 330. The report is itself a denial. | S05 (5 confirmed + 1 cleared visible cases on the customer). Independent: typical amount (z −0.65), region seen 7× in the last 7 days **[F]**. | S15 (region ≠ modal; 52 regions on the card). | none | customer_validation → *simulated* "made it" | CLOSE_NO_FRAUD (auto) | legitimate / closed | The customer wrote "I never made this purchase"; the simulator has the same customer confirm it. CREATE_CASE dropped although a dispute always opens a case (3a). | "You closed a customer complaint by simulating the complainant recanting." | **HIGH** |
| **HHG-004** | customer_report, online $128.33, Product C. | Independent: 4.6× the online median (z 2.07, 99th pct), first use of this device on the card **[F]**. Agent rated it weak: S08 misses at the variance floor. | S05, S10 (anti-informative alone), S13. | none | customer_validation → no_response | STEP_UP_AUTH, MONITOR_CARD, CREATE_CASE (auto), same as initial | uncertain / open | Initial action says STEP_UP_AUTH but the request actually made is customer_validation (see finding 6). R4 (no reply → MONITOR + DECLINE) not applied. | "Your recommendation and your request disagree." | MEDIUM |
| **HHG-005** | risk_score 0.54, online $100.07, Product R. | S05 only. Independent: amount ordinary (z 0.74); iOS profile shared by 110 customers (a common device). | S10 "New" (98% of cleared alerts are New). | none | step_up_auth → *simulated* pass | ALLOW_TRANSACTION, MONITOR_CARD (auto) | legitimate / closed | Plausibly legitimate on the evidence; verdict still driven by the draw. No CREATE_CASE (3a). | "Why is there no case when you asked for evidence?" | LOW |
| **HHG-006** | customer_report, online $482.12, Product C. | S07 burst [MEDIUM]. Independent, **not used by the agent**: an in-person card (195 of 201 txns) whose only prior online spend was $25–50 (Product H) suddenly makes 4 online Product C purchases of $457–488 within ~30 minutes, alternating two devices (Windows/IE11 and iOS Safari) and two billing regions (264/476) **[F]**. | S10. | **undocumented** | customer_validation → *simulated* denial | BLOCK_CARD (L1), CREATE_CASE, FILE_REPORT (L2) | fraud / closed_fraud | Evidence genuinely supports suspicion, but the agent missed its strongest features (channel shift, device/region alternation) and labelled a 4-in-30-minutes burst "undocumented" although README pattern 2 (burst of 2–4, amounts not fitting history) and pattern 5 (mixed channel) both fit. R9 is about abuse "across customers", which this is not. | "The README describes this burst and you called it undocumented." | MEDIUM |
| **HHG-007** | risk_score 0.87, in-person $111.92, region 264. | S05 [LOW]. Independent: behaviour is entirely normal: region 264 is 93% of the card's in-person history and 94 txns there in the last 7 days; z 0.31. The customer has 86 closed-case txns (18 confirmed frauds) **[F]**. | Model score 0.87, contradicted by behaviour (recorded as a conflict **[D]**). | none | customer_validation → no_response | VERIFY_WITH_CUSTOMER, MONITOR_CARD (auto) | uncertain / open | Verdict is defensible. R4 not applied; no case (3a). | "0.87 and 18 prior frauds, and you only 'monitor'?" | LOW |
| **HHG-008** | customer_report, online $55.68, Product C. | S05. Independent: all *contradicts* fraud: typical amount (z 0.44), device previously used by the card ("Found"), 899 prior online txns **[F]**. | S13. | **none** | customer_validation → *simulated* denial | BLOCK_CARD (L1), CREATE_CASE (auto) | **fraud** / closed_fraud | A "fraud" verdict and a card block resting solely on a simulated statement; pattern `none` for a fraud verdict ("what kind of fraud?"); 2 other same-hour card txns never examined. | "Fraud, but you can't say what kind, and the data disagrees." | MEDIUM |
| **HHG-009** | customer_report, online $30.02, Product S. | None: nothing fires; typical amount. | Device profile is the empty `? \| ? \| ? \| ?` (1,001 customers). | none | customer_validation → *simulated* "made it" | CLOSE_NO_FRAUD (auto) | legitimate / closed | Same as HHG-003. | "The customer said they didn't; a hash said they did." | **HIGH** |
| **HHG-010** | risk_score 0.90, online $1,000.03, Product R. | S08 [MEDIUM]: z 4.34, 18× the online median ($54.98), largest ever **[D]**. Independent: new device (Edge/Win10), anonymous.com email. | S10 (anti-informative alone). | card_not_present_fraud | step_up_auth → *simulated* fail | DECLINE_TRANSACTION (L1), CREATE_CASE, VERIFY_WITH_CUSTOMER, MONITOR_CARD, ESCALATE_TO_ANALYST (auto) | uncertain / escalated | Defensible under R1/R8, arguably an under-call for 18× + failed step-up. Declines a transaction that is 3 h old (presumes it is pending). README pattern 3 (new device) fits better than 2. | "Failed authentication and you still say 'uncertain'?" | LOW |
| **HHG-011** | customer_report, online $131.30, Product C. | None validated. Independent: this card has **10,306 prior online txns (1,454 in 30 days, 59 in 48 h)**: machine-like velocity; first use of a device seen on only 3 customers ever **[F]**. | S10, S13. | none | customer_validation → no_response | STEP_UP_AUTH, MONITOR_CARD, CREATE_CASE (auto), same as initial | uncertain / open | Velocity and card atypicality ignored (no such signal exists); 5 later txns on the card visible at `as_of` unexamined; initial STEP_UP vs request mismatch; R4. | "Your agent never noticed a card with 10,000 transactions." | MEDIUM |
| **HHG-012** | risk_score 0.55, in-person $30.91, region 494. | S05. Independent: typical amount (z −0.77), region seen before **[F]**. | S15 (region ≠ modal; 40 regions, so weak). | **none** | customer_validation → *simulated* denial | BLOCK_CARD (L1), CREATE_CASE (auto) | **fraud** / closed_fraud | As HHG-008: fraud + block from a simulated denial on ordinary behaviour. | "Fraud on a $30 in-person purchase in a known region." | MEDIUM |
| **HHG-013** | risk_score 0.76, online $35.66, Product C. | S05. Independent: ordinary amount (z −0.62). | S10, S13; new device string `Windows \| ? \| chrome 66.0 \| ?` (59 customers, all in the last 7 days: an unspecific bucket, not a device). | none | step_up_auth → *simulated* fail | DECLINE_TRANSACTION (L1), CREATE_CASE, VERIFY_WITH_CUSTOMER, MONITOR_CARD (auto) | uncertain / open | Fine; but the "fail" is random. | none beyond finding 1 | LOW |
| **HHG-014** | analyst_request (ring hunt), card C13487-K1, txn 3478561 (online $74.96, Product C, New, anonymous proxy). | **S01 ring [HIGH] [D]**: the device profile has 44 customers / 80 txns by `as_of`, 100% `New`, 100% proxied, 100% Product C; of the 319 profiles with ≥30 customers it is the only one meeting both the new-device (≥0.9) and proxy (≥0.5) thresholds **[F]**; 26 txns / 20 customers in the last 30 days; 4 customers / 10 txns in closed *fraud* cases, 0 cleared **[F]**. | S10/S11/S12: these are the *same facts* S01 is built from, yet counted as a second "independent source". | undocumented (ring) | step_up_auth → *simulated* pass | **ESCALATE_TO_ANALYST (auto) only** | fraud / escalated | Final drops CREATE_CASE, FILE_REPORT (L2) and MONITOR_CONNECTED_CARDS that R6/R9/3a require; the flagged customer's step-up says nothing about 19 other cards. SAR reason is circular. Verdict `fraud`, yet no report. "2 independent sources" is overstated. All 26 txns are treated as one episode on a device-sharing basis alone. | "Your showcase case files no report and tells nobody to watch 19 cards." | **HIGH** |
| **HHG-015** | risk_score 0.77, online $599.94, Product R. | Independent: 6× the online median ($99.96), largest amount the card has made (raw z 2.77), first use of an IE11 device seen on 6 customers, anonymous.com email **[F]**. | S05, S10. | none | step_up_auth → *simulated* pass | ALLOW_TRANSACTION, MONITOR_CARD (auto) | legitimate / closed | False-negative candidate: S08 does not fire because of the `Z_VAR_FLOOR = 0.25` variance floor. Allowed on a random pass; no case. | "The largest purchase the card has ever made, from a new device, and you allow it." | MEDIUM |
| **HHG-016** | customer_report, online $59.67, Product C. | S10/S13 only. Ordinary amount. | New-device profile shared by 143 customers. | none | customer_validation → *simulated* "made it" | CLOSE_NO_FRAUD (auto) | legitimate / closed | Same as HHG-003. | as HHG-003 | **HIGH** |
| **HHG-017** | risk_score 0.57, online $100.09, Product R, hidden proxy. | S11 [LOW]. Independent: device previously used by this card, amount below its median ($199.92). | S11 alone: lift 1.5, 1.45% of flagged are cleared. | none | step_up_auth → no_response | STEP_UP_AUTH, MONITOR_CARD (auto), same as initial | uncertain / open | R4 not applied; no case (3a). | "R4 exists and you skipped it." | LOW |
| **HHG-018** | customer_report, in-person $39.08, region 126. | S05. Independent: home-region purchase (modal share 0.83, 33 txns there in 7 days), typical amount; the card has 5,860 prior txns **[F]**. | S15. | **none** | customer_validation → *simulated* denial | BLOCK_CARD (L1), CREATE_CASE (auto) | **fraud** / closed_fraud | Blocks a very active card on a simulated denial; 2 later txns on the card unexamined. | as HHG-008 | MEDIUM |
| **HHG-019** | risk_score 0.90, online $99.92, Product R. | None. Independent: typical amount (z 0.47); new device seen on only 3 customers. | S05, S10. Score 0.90 recorded as an unresolved conflict **[D]**. | none | step_up_auth → *simulated* pass | ALLOW_TRANSACTION, MONITOR_CARD (auto) | legitimate / closed | A 0.90 alert with no corroboration is discounted entirely; the README warns scores are unreliable in both directions. Defensible, disclose. | "0.90 and nothing?" | LOW |
| **HHG-020** | risk_score 0.52, online $125.08, Product R. | None. | Only 4 prior online txns, so S08 cannot be evaluated; S09 new product. | none | step_up_auth → *simulated* pass | ALLOW_TRANSACTION, MONITOR_CARD (auto) | legitimate / closed | Thin online history means no anomaly test was possible; the agent says so. | none beyond finding 1 | LOW |

Severity counts: **HIGH 4** (HHG-003, 009, 014, 016), **MEDIUM 9**, **LOW 7**, **CRITICAL 0**, **NONE 0**.

---

## Phase 2: adversarial attacks

### A. False-positive attack: can a legitimate transaction be wrongly escalated?

- **Simulated denial → block (real mechanism).** HHG-008, 012, 018 end as `closed_fraud` with `BLOCK_CARD` because the draw said "denied". Their own evidence *contradicts* fraud: typical amounts, known devices or home regions. **[D]** If the customer's denial were real that would be R2-correct; because it is simulated, the block is a function of the seed.
- **S01 has a low floor.** `RING_MIN_CUSTOMERS = 3` (`patterns.py:11`). At HHG-014's `as_of`, exactly two profiles satisfy the S01 thresholds: the real ring (44 customers) and `SAMSUNG | Android 7.0 | samsung browser 6.2 | 1280x720` (4 customers, 4 txns, 100% `New`, 50% proxied) **[F]**. A household or an office behind one VPN would look the same. S01 is rated HIGH by fiat: its own documentation says S01 recalls 0% of ordinary fraud and was validated on the single seeded ring. **[F]**
- **Shared device / region / new device / proxy alone.** Handled well: S10 and S12 are demoted ("anti-informative", lift 0.74), degrees > 20 give no sharing evidence (lift ≈ 1.0), and none of these alone raises strength above `weak`. **[D]** This is a real strength.
- **Amount anomaly (S08).** Only one MEDIUM signal in the benchmark could cause a false positive (HHG-002, 010); both stay below `fraud`.
- **Risk score.** Not used as evidence beyond a coarse band, so it cannot cause escalation. **[D]**
- **Prior-case similarity.** GraphRAG results are labelled "memory and wording context, not proof" and are `CONTEXT` rated. **[D]**

### B. False-negative attack: can fraud be missed?

- **The only strong discriminator for ordinary fraud is deliberately unused.** The project's own validation shows `risk_score` AUC 0.78 (online) / 0.88 (in-person), with precision at ≥ 0.7 of 36% online and 49% in-person **[F]**. The README says a score is "a reason to look, never a verdict", so the agent uses it only as a band. Result: HHG-019 (0.90) and HHG-007 (0.87) get no evidential weight. Defensible, but the agent then has *no* signal for typical single-transaction fraud: the docs state graph signals recall ≈ 0–8% of it. **[F]**
- **S08 variance floor.** HHG-015 (raw z 2.77, largest ever) and HHG-004 (raw z 2.07) fall below the threshold only because of `Z_VAR_FLOOR = 0.25`. **[D]**
- **Missing signal classes.** No velocity signal (HHG-011: 10,306 prior txns), no channel-shift signal (HHG-006), no device/region alternation (HHG-006). The README explicitly allows the unnamed V/C/D/M features, and the project's own validation (S16) found some informative (e.g. C1 within-channel AUC 0.79), yet none of the agent's signals uses them. **[F]**
- **Evidence spread across weak signals.** By design weak signals do not stack ("cannot lift P above ~0.5 by stacking"). A real fraud spread over several weak features is therefore invisible. **[D]**
- **No prior case / low degree.** S02a/b need visible confirmed fraud on the device; a rare device (HHG-019: 3 customers, HHG-013) with no history gets nothing. **[D]**
- **Exposure under-counting.** For denied customer reports the exposure is the flagged transaction only; the agent never looks for other same-card transactions between the flagged time and `as_of` (visible later txns: HHG-011 ×5, HHG-007 ×3, HHG-018 ×2). **[F]** Whether they are fraud is [U].

### C. Ring attack: what does the shared-origin ring prove?

**What it proves (from data visible at `as_of`) [F]:** one device profile appears on 44 unrelated accounts, on *every* occurrence it is flagged new to the account and behind an anonymising proxy, and only for Product C; two separate bursts (days 44–64 and 135–143); it is the only one of 319 comparable profiles (≥30 customers) that meets both the new-device and proxy thresholds. Its early burst is enriched for confirmed fraud: 10 of 54 txns (18.5%) sit in closed fraud cases and none in cleared, against 3.4% for all txns and 12.2% for the matched Product C + New + proxy pool. Coordinated activity is a reasonable inference. **[I]**

**What it does *not* prove:**
- That each of the 26 recent transactions is fraud. 81% of the early burst was never in a closed case (unlabelled is not "legitimate", but it is not "confirmed" either). **[F]**
- That the 20 customers are victims rather than users of a shared proxy/VPN, a reseller kiosk or a device farm. The profile string is model + OS + browser + screen; it is *not* a device identifier. **[I]**
- That the flagged customer's own transaction is fraudulent: the ring makes it a lead, not a finding. R1 in fact says verify individual customers.
- That "4 of 44 customers with visible fraud" is enrichment: the population rate of customers with visible confirmed fraud is 11.1%, and this profile's share is 9.1%. At customer level there is **no enrichment**; the enrichment is at transaction level and rests on 10 transactions. **[F]**
- That S01's HIGH rating is validated: it was built and tested on this same ring and has no independent labelled precision. **[F]**

The agent's evidence *description* is fair (device ring, "context, not proof" for connected cards). Its *summary* is not: it calls the evidence "strong validated evidence; 2 independent source(s)", where the second source is the identity flags that define S01. **[D]**

### D. Temporal attack

Attempted leakage channels and result:

| Channel | Result |
|---|---|
| Transactions cited in the 20 answer files | 39 ids checked: **0 later than `as_of`**. **[F]** |
| HHG-014 connected-card expansion | Oldest cited txn 8.1 days before `as_of`, newest 3.2 h before; all inside the 30-day window; none belongs to a closed case. **[F]** |
| Closed cases / prior cases / GraphRAG | The last closed case closed on day 128; the first benchmark `as_of` is day 133; no closed-case transaction is dated after the last close date. No cited prior case closes after `as_of`. The gating logic is therefore *never exercised* by the benchmark, so it is safe but untested against real leakage. **[F]** |
| Case memory | Benchmark `FI_Case` vertices now exist in the graph, including ones closed on simulated replies. They are not in the TextChunk corpus (5,594 chunks, 0 benchmark ids), so they cannot feed retrieval today. **[F]** |
| Design-time knowledge | S01 was designed after inspecting the seeded ring in the Aug–Oct history. Its second burst is in the benchmark window. That is out-of-time for the burst but not blind: one ring, one rule. **[F]/[I]** |
| Derived features | `id_15 = New`, D/C/V columns are publisher-side row features, not built from later rows as far as the data shows. **[U]** whether the publisher's `New` flag itself used later data. |

**Biggest temporal-risk area:** design-time exposure of S01 to the seeded ring, not runtime leakage.

### E. Policy attack (R1–R10, 3a, 3b)

| Rule | Finding |
|---|---|
| R1 | Followed: no block without a customer denial. |
| R2 | Followed *given the simulated denial*. Routes correct (BLOCK_CARD ≤ $2,500 → L1; FILE_REPORT → L2). |
| R3 | Followed, but on a simulated confirmation that contradicts the trigger for customer reports (HHG-003/009/016). |
| **R4** | **Not applied.** The simulator's `no_response` is normalised to `pending` (`orchestrator.py:24`), so the `no_reply` branch in `actions.py` never runs. HHG-004/007/011/017 keep their initial actions instead of MONITOR_CARD + DECLINE_TRANSACTION (+ ESCALATE if > $500). |
| R5 | Not triggered by any case. |
| **R6** | **Violated in HHG-014's final actions.** Several cards show confirmed fraud on this profile at `as_of` (4 customers), so CREATE_CASE + FILE_REPORT + MONITOR_CONNECTED_CARDS are required; the final drops all three (`actions.py:96-98`). |
| R7 | Not evaluable: there is no merchant field. The agent states this in every case. **[U]** |
| R8 | Followed (HHG-010, 014). |
| **R9** | Same drop as R6 for HHG-014. |
| R10 | Followed (never used). |
| **3a** | 9 initial-action lists request evidence without CREATE_CASE (HHG-001, 005, 007, 012, 013, 015, 017, 019, 020); 12 final lists lack it although evidence was requested; the 3 customer-report closures drop it despite "customer dispute always opens a case". |
| 3a (SAR) | HHG-014's "No report" reason cites its own final actions (`sar.py:76`): circular. The condition (fraud strongly suspected AND shared device AND exposure > $1,000) is met on the case's own facts. |
| 3b | Initial vs final are recorded; "what changed" is generic ("the X response changed the recommendation"). |

Legitimate cases producing fraud-style actions: none (the legitimate closures are all `CLOSE_NO_FRAUD` / `ALLOW`). The reverse problem is the real one: fraud-style closures produced by a coin.

### F. Agentic attack: LLM vs deterministic

| Question | Answer |
|---|---|
| What comes from LLM reasoning? | Which of the 10 read-only tools to call and in what order; the evidence-request *type* where the trigger is not a customer report; a `sufficient` flag and a ≤300-char rationale appended to `stop_reason`. |
| What is deterministic authority? | Signals, evidence strength, pattern, verdict, actions, routes, exposure, SAR, case validation, and the explanation text (template). The LLM cannot change any of them. |
| Where does the LLM add value? | Nowhere measurable in decisions. It can add tool-order variety and a free-text rationale. |
| Was the submission produced by the LLM? | **No.** All 20 files have `tokens: 0`; they are the deterministic path. |
| Real-model results | On the free model the measured runs were: 3 of 4 cases fell back (one provider error, two token-budget stops); the budget fix was verified offline only and the one live retry hit HTTP 429. The successful case followed the standard tool order. |
| Does fallback mask a weakness? | Yes. The eval metric "same authoritative case as baseline: 4/4" is true *by construction* (the deterministic layer forces required evidence and owns every decision). It cannot detect a weak LLM. A 75% fallback rate is a weakness that this metric hides. |
| Is deterministic fallback equivalent to LLM reasoning? | No, and this audit does not treat it as such. |

**Biggest agentic weakness:** the project is described as LLM-orchestrated, but the LLM is untested on the benchmark, never completed more than 25% of the cases it was run on, and has no authority; the submitted evidence is entirely deterministic.

---

## Cross-case findings

1. **Verdicts follow the simulator.** Re-evaluating the agent's own decision functions on each stored record: **19 of 20 verdicts change with the simulated reply**. Evidence-only (`pending`) gives `uncertain` ×19, `fraud` ×1. Of the submitted verdicts, 13 are set by a simulated reply (9 `legitimate` from simulated "confirm/pass", 4 `fraud` from simulated "denial"), 6 are `uncertain` (2 from a simulated fail, 4 from `no_response`), 1 (HHG-014) is evidence-driven. **[D]**
2. **Strongest part:** deterministic provenance and temporal discipline, honest handling of noisy signals (New device, proxy, shared-hub devices, risk score), correct approval routing, and refusal to invent a probability.
3. **Weakest part:** decision-layer conformance with the policy text (R4, R6/R9, 3a), and the reliance on an assumption generator for verdicts.
4. **False-positive mechanism:** simulated denial → BLOCK_CARD → `closed_fraud` on weak or contradicting evidence (HHG-008, 012, 018); and S01 with a 3-customer floor.
5. **False-negative mechanism:** no signal for typical single-transaction fraud (risk score deliberately unused), the S08 variance floor (HHG-015), and no velocity / channel-shift / device-alternation features (HHG-006, 011).
6. **Most questionable pattern:** HHG-006 "undocumented" (README patterns 2 and 5 fit); three `fraud` verdicts with pattern `none` (HHG-008, 012, 018); HHG-002/010 `card_not_present_fraud` from a single signal.
7. **Most questionable action:** HHG-014's ESCALATE-only final; `CLOSE_NO_FRAUD` on customer disputes (HHG-003/009/016).
8. **Also:** in 6 of 8 customer reports the initial action is `STEP_UP_AUTH` but the request actually made is `customer_validation` (HHG-004, 006, 008, 009, 011, 016). The explanation text also lists "customer response not available yet" as unresolved after the response arrived.
9. **Biggest temporal-risk area:** design-time exposure of S01 to the seeded ring; the gating code is correct but the benchmark never exercises it.
10. **Biggest agentic weakness:** see F.

---

## Submission Risk

**RED**: material investigation-quality problems exist in the decision layer.

The evidence layer alone would be YELLOW: weaknesses are explainable and contained (S01 rests on one ring, S08 floor, unused features). The decision layer is not: HIGH issues 1 to 3 below affect the answers judges score (verdict, case vs report, final actions on the showcase case), and the verdict distribution is set by an assumption generator. This can be brought to YELLOW with the targeted changes below, none of which need calibration, a probability, ML, or any change to temporal safeguards.

---

## Phase 4: HIGH-severity issues: responsible code, smallest safe fix, regression surface

Nothing below has been implemented.

### H1. Customer reports closed via a contradicting simulated reply (HHG-003, 009, 016), and verdicts driven by the draw

- **Code:** `simulator.py:21-23` (`DISTRIBUTION`); `orchestrator.py:369-373` (request type for customer reports is `customer_validation`); `orchestrator.py:404-418` (verdict rules); `actions.py:91-95` (`confirmed` → `CLOSE_NO_FRAUD`).
- **Smallest safe fix:** for `customer_report` triggers, never draw `verified_legitimate` (a customer cannot both report and confirm); use `denied_or_unrecognized` (the report stands) or `no_response`. Keep CREATE_CASE in the final actions of a customer dispute (3a).
- **Bigger, riskier decision (needs your call, not a bug):** decouple the verdict from the simulated reply, so a weak-evidence case stays `uncertain` while the *actions* still follow the stated assumption, as the README allows ("let `final` reflect that assumption"). Whether the hidden key rewards `uncertain` or a concrete verdict is [U].
- **Tests that could regress:** `test_simulator_exposure` (simulator distribution and case outcomes), `test_agent_units` (`confirmed` branch), `test_answer_sar`, `test_case_write_live` (idempotency / revision), `test_ui_api` and the UI fixtures.

### H2. HHG-014 final actions drop R6/R9/3a actions; circular SAR reason

- **Code:** `actions.py:91-98` (`confirmed` and `passed` with strong evidence return `[ESCALATE_TO_ANALYST]` only); `sar.py:76` (reason derived from final actions).
- **Smallest safe fix:** in those two strong branches return the initial R6/R9 actions (CREATE_CASE, FILE_REPORT, MONITOR_CONNECTED_CARDS) plus ESCALATE_TO_ANALYST; evaluate the SAR condition from the case facts (verdict, shared origin, exposure), not from the presence of FILE_REPORT.
- **Tests that could regress:** `test_agent_units.py:219`, `test_simulator_exposure.py:322`, `test_ui_api.py:105`, UI `app.test.tsx` fixtures/assertions for HHG-014, the `cases/HHG-014.json` file, `test_agent_live.py`, and the final-action expectations in `docs`. Re-running the pipeline would create `FI_Case` revision 2 for HHG-014.

### H3. 3a: CREATE_CASE omitted when evidence is requested / dispute closed

- **Code:** `actions.py:65-74` (weak branch adds CREATE_CASE only for customer reports), `actions.py:91-95` (confirmed drops it).
- **Smallest safe fix:** add CREATE_CASE to the initial actions whenever an evidence request will be made (`unc.needs_more_evidence`), and keep it when closing after a confirmation.
- **Tests that could regress:** `test_agent_units` (exact action lists), `test_answer_sar`, `test_simulator_exposure`, all 20 answer files, UI fixtures.

### MEDIUM items that should be fixed or disclosed rather than left silent

- **R4 never applied** (`orchestrator.py:24` maps `no_response` → `pending`). Either treat a simulated `no_response` as "24 h elapsed" or disclose that it means "still waiting".
- **Initial action vs request type mismatch** for online customer reports (`actions.py:65-68` vs `orchestrator.py:372`).
- **"2 independent sources" for HHG-014** counts identity flags that are components of S01 (`orchestrator._assess`: `n + (1 if low else 0)`).
- **Stale text:** "customer response not available yet" remains in `missing` after a response.
- **Exposure for denied customer reports** counts only the flagged transaction.

---

## What to disclose to judges (if left as is)

1. Verdicts for 19 of 20 cases depend on a deterministic **simulated** customer/step-up reply drawn from a hash; with no reply the evidence supports `uncertain` (and `fraud` only for HHG-014).
2. `fraud_probability` is null; no calibrated probability is supportable from the supplied data.
3. R7 cannot be evaluated (no merchant field); R4 is interpreted as "still waiting" for a simulated no-response.
4. S01 (shared-origin ring) rests on one seeded ring and has no independent labelled precision; it is HIGH by design, not by validation.
5. The submitted results come from the deterministic pipeline (`tokens: 0`). The LLM orchestration layer was exercised on historical cases only, has no decision authority, and fell back on most of those runs.
6. No accuracy is claimed: there is no answer key.
