# Signal validation — Phase 4 (before any TigerGraph work)

Purpose: test every signal proposed in `docs/investigation_findings.md` and `docs/graph_architecture.md` against the July–October labelled data, using **point-in-time (PIT)** computation, and grade each signal HIGH / MEDIUM / LOW.

## 0. Method, and what changed relative to earlier phases

**Evaluation window.** July 2 – July 31 is warm-up (history is still building). Metrics use **August 1 – October 31** transactions. Nothing after 2016-10-31 is used in any tuning or metric (§3 explicitly, §6 for the audit).

**Point-in-time rule.** For a transaction at time *T*, a signal may use only rows with `epoch ≤ T` and closed cases with `closed_at ≤ T`. Device/entity aggregates were computed incrementally and cross-checked against a brute-force filtered-view oracle (§6, 0 mismatches in 400 checks).

**Metrics.** Positives = the 10,810 labelled confirmed-fraud txns in the window (of 287,109). Everything else is **unlabeled, not verified legitimate**, so *precision is a lower bound* and lift is judged against the **channel-specific base rate** (online 10.6%, in-person 2.1%, identity-bearing online rows 10.9%). 384 cleared-alert txns are counted separately as `cleared` (false alarms that were reviewed).

### 0.1 Corrections to earlier findings (important)

| Earlier claim | What validation shows | Consequence |
|---|---|---|
| `id_15 = New` lift 1.3 (Findings §2.4) | That lift used the all-channel base rate. Against the online base: precision 7.8% vs 10.6% → **lift 0.74**; 1.27% of flagged are cleared alerts | New device alone is *anti*-informative |
| V93 / V79 / V52 AUC 0.91–0.93 for CNP (Findings §2.5) | Confounded by channel (in-person baseline). Online-vs-online: V93 **0.55–0.57**, V52 0.60–0.65 | Drop V93/V52/V79 as evidence |
| Device sharing "moderate" at degree 21–100 (Findings §5.3) | Measured cleanly here: lift **≈ 1.0** (10.6% vs base 10.9%) at degree 21–100, 0.94 at > 100 | Only degree ≤ 20 carries sharing evidence |
| "Region ≠ home" 89% vs 47% (Findings §2.1) | "Home" used the whole-period mode (future information). PIT: away-from-modal 87% (OOR) vs 55% non-fraud, but 76% of cleared alerts are also "away" and overall lift is 1.13 | Region novelty is weak evidence |
| "Shared-fraud lift 26–71% at low degree" | Confirmed in direction, re-measured cleanly (§2.2): 49.7% / 27.2% precision at ≤ 5 / 6–20 | Kept, with numbers below |

---

## 1. Ring-detection rule (compound device anomaly)

**Definition.** A `DeviceProfile` is *ring-suspect* at time `T` if, using only txns ≤ `T`:
`customers ≥ 3 ∧ new_share ≥ 0.9 ∧ proxy_share ≥ 0.5` where `new_share` = share of the profile's txns with `id_15 = New`, `proxy_share` = share with any `id_23` proxy value. (The `n_known ≥ 3` gate from Phase 3 was **tested and found unnecessary** — §1.3.) No label and no `risk_score` is used.

### 1.1 Historical result (data ≤ 2016-10-31 only)

| Question | Result |
|---|---|
| Is the ring distinguishable without any November/December data? | **Yes.** The Aug–Sep wave (54 txns, 24 customers, 4 closed `undocumented` cases) is flagged on **2016-08-16 14:11**, when only **3 customers** had used the profile |
| Coverage of the ring's txns | Flag was active on arrival for **52 of 54** txns (96%); only the first two txns (Aug 15) arrived before it |
| Lead over the labels | First closed case became visible on **2016-08-31**, **14 days after** the flag; **41 ring txns** arrived in that gap. The rule works before any label exists |
| Labelled ring txns caught | **10 / 10** (flagged on arrival); 42 additional *unlabeled* ring txns flagged |
| Other confirmed fraud (10,780 txns) flagged by this rule | **0** (rule is specific to the shared-origin pattern; it is not a general fraud detector) |
| Cleared alerts flagged (384 txns) | **0** |
| Steady-state alert volume (Aug–Oct) | **1 alert** (the ring). July shows 5 more alerts, all warm-up artifacts (first 2 weeks of history) |

### 1.2 Threshold sensitivity (ever-flagged = alerts a monitor would have raised; end-state = flagged on Oct 31)

| Config | Ever flagged | End-state flagged | Ring found | Ring first flag | New alerts/month (Jul, Aug, Sep, Oct) |
|---|---|---|---|---|---|
| **cust ≥ 3, new ≥ 0.9, proxy ≥ 0.5 (proposed)** | 6 | 2 | ✔ | Aug 16 | 5, 1, 0, 0 |
| cust ≥ 3, new ≥ 1.0, proxy ≥ 0.5 | 6 | 2 | ✔ | Aug 16 | 5, 1, 0, 0 |
| cust ≥ 3, new ≥ 0.9, proxy ≥ 0.7 | 2 | 1 | ✔ | Aug 16 | 1, 1, 0, 0 |
| cust ≥ 5, new ≥ 0.9, proxy ≥ 0.5 | 2 | 1 | ✔ | Aug 17 | 1, 1, 0, 0 |
| cust ≥ 3, new ≥ 0.8, proxy ≥ 0.5 | 8 | 3 | ✔ | Aug 16 | 6, 2, 0, 0 |
| cust ≥ 3, new ≥ 0.7, proxy ≥ 0.5 | 22 | 6 | ✔ | Aug 16 | 13, 5, 4, 0 |
| cust ≥ 3, new ≥ 0.9, proxy ≥ 0.3 | 27 | 4 | ✔ | Aug 16 | 17, 3, 5, 2 |
| cust ≥ 2, new ≥ 0.9, proxy ≥ 0.5 | 42 | 18 | ✔ | Aug 15 | 28, 5, 4, 5 |

Full grid (cust ∈ {2,3,5} × new ∈ {0.7,0.8,0.9,1.0} × proxy ∈ {0.3,0.5,0.7,0.9}, 48 configs): **the ring is found in all 48**; the only thing that varies is false-alert volume (0–69 devices). The ring sits at new = proxy = 1.0, so **the ring cannot discriminate between thresholds** — the thresholds are chosen by alert budget, not fitted to ring behaviour.

### 1.3 Ablation (cust ≥ 3)

| Variant | Devices flagged (ever) | Ring found |
|---|---|---|
| `new ≥ 0.9` only | 439 | ✔ |
| `proxy ≥ 0.5` only | 82 | ✔ |
| new **and** proxy (proposed) | **6** | ✔ |
| + `n_known ≥ 3` / `≥ 4` / `txns per customer ≤ 3` / `customers ≤ 100` | 6 (no change) | ✔ |
| proxy ≥ 0.5 ∧ visible confirmed fraud ≥ 1 (label-based) | 14 | ✔ but first flag **Aug 31** (15 days later), only 10 txns after flag |
| visible confirmed fraud ≥ 2 on device, degree ≤ 100 (label-only) | 155 | ✘ **ring not found** |

Both conditions are necessary; the label-based variants are later and noisier. The extra gates from Phase 3 add nothing and are dropped to keep the rule simple.

### 1.4 False-positive behaviour
Non-ring devices flagged (ever) under the proposed config: 5 iOS/Samsung profiles with 3–6 customers, 4–860 txns, labelled-fraud share of their txns 6.9% (below base 10.9%), all flagged in the first 6 weeks (history still small). End-state Oct 31: the ring plus one 4-customer / 4-txn profile with no fraud. **Expected false alerts after warm-up: ≈ 0 per month at the proposed thresholds.**

### 1.5 Caveats (do not over-claim)
- **n = 1 historical ring.** Detection rate 1/1 with FP volume ≈ 0 is encouraging but statistically thin; it does not validate the rule for a differently shaped ring (e.g. without a proxy).
- The thresholds were first written after seeing the ring; the grid shows the ring is insensitive to them, but their *false-alert* behaviour is calibrated only on 4 months of data.
- The Nov–Dec wave was **not** used here. Its detection at as-of 2016-11-12 and 2016-11-22 (Phase 3: 24 / 44 customers, flagged, one other 4-customer profile) is a confirmation on the target period, not validation.

**Temporal safety:** HIGH (only rows ≤ `T`; §6). **Rating: HIGH** as evidence that a device is a shared-origin cluster (connected-cards discovery, R6/R9). It supports **coordination**; it does not by itself prove each customer's txn is fraud (42 of 52 ring txns are unlabeled) — pair with step-up/verification (R1) before a block.

---

## 2. Shared-entity signals and hub exclusion

### 2.1 Definition of the sharing signal
`S02`: at txn time `T`, **another** customer has a confirmed-fraud closed case (`closed_at ≤ T`) involving a txn on the same entity (device, region within 14 d, email domain within 30 d). Stratified by the entity's **as-of degree** (distinct customers seen ≤ `T`, including self).

### 2.2 Device profile (identity-bearing txns, Aug–Oct, base fraud 10.9%)

| As-of degree | n_known | Txns in class | Flagged by S02 | Fraud | Cleared | Precision | Lift |
|---|---|---|---|---|---|---|---|
| ≤ 5 | ≥ 3 | 4,153 | 175 | 87 | 0 | **49.7%** | **4.6** |
| ≤ 5 | < 3 | 7,087 | 340 | 176 | 0 | **51.8%** | 4.8 |
| 6–20 | ≥ 3 | 3,435 | 743 | 202 | 4 | **27.2%** | **2.5** |
| 6–20 | < 3 | 4,502 | 1,806 | 434 | 2 | 24.0% | 2.2 |
| 21–100 | ≥ 3 | 6,609 | 3,198 | 339 | 39 | 10.6% | **0.97** |
| 21–100 | < 3 | 4,026 | 3,411 | 403 | 6 | 11.8% | 1.1 |
| > 100 | ≥ 3 | 6,892 | 6,076 | 617 | 76 | 10.2% | 0.94 |
| > 100 | < 3 | 17,127 | 17,094 | 1,582 | 32 | 9.3% | 0.85 |
| null profile (`n_known = 0`) | 0 | 1,115 | 1,115 | 44 | — | 3.9% (base 10.9%) | **0.36** |

- Allowing **all** degrees: 32,843 txns flagged, precision 11.7% (lift 1.07) — the hubs drown the signal (91% of flagged txns come from degree > 20).
- Restricted to `deg ≤ 20` and `n_known ≥ 3` (Phase-3 rule): 918 flagged, precision 31.5%, cleared 0.4%, **recall 2.7% of confirmed fraud** in the window (`deg ≤ 20`, any `n_known`: 3,064 flagged, 29.3%, recall 8.3% of non-undocumented fraud).
- Ring txns are **not** caught by S02 (0 flagged at ≤ 20, it lives at degree 22–24) → S02 and §1 are complementary.

### 2.3 Region and email

| Entity (window) | As-of degree | Channel | Flagged | Fraud | Precision | Base | Lift |
|---|---|---|---|---|---|---|---|
| Region, 14 d | ≤ 20 | online | 8 | 3 | 37.5% | 9.6 | 3.9 |
| | 21–100 | online / in-person | 489 / 647 | 30 / 20 | 6.1% / 3.1% | 9.6 / 2.1 | 0.64 / 1.49 |
| | 101–299 | online / in-person | 1,171 / 12,614 | 153 / 400 | 13.1% / 3.2% | | 1.37 / 1.53 |
| | ≥ 300 | online / in-person | 20,490 / 212,071 | 1,934 / 4,237 | 9.4% / 2.0% | | 0.99 / 0.96 |
| `R_emaildomain`, 30 d | ≤ 20 | online | 36 | 31 | 86.1% | 10.7 | 8.1 |
| | 21–100 | online | 1,330 | 100 | 7.5% | | 0.71 |
| | 101–299 / ≥ 300 | online | 2,081 / 48,337 | 266 / 5,286 | 12.8% / 10.9% | | 1.20 / 1.03 |
| `P_emaildomain`, 30 d | ≤ 20 | online | 15 | 10 | 66.7% | | 6.3 |
| | 21–299 | online | 1,198 / 691 | 84 / 33 | 7.0% / 4.8% | | 0.66 / 0.45 |
| | ≥ 300 | online | 47,990 | 5,285 | 11.0% | | 1.03 |

Low-degree region/email links look strong but rest on **8 / 36 / 15 flagged txns** (a handful of entities and cases) — statistically fragile. At degree > 20 there is no lift anywhere (0.45–1.5 with no consistent direction).

### 2.4 Validated hub rules

| Rule (phase-3 wording) | Verdict | Revised rule |
|---|---|---|
| Null profile = blocked | **Confirmed.** Precision 3.9% vs 10.9% base; the null profile alone links **1,115 txns**; at benchmark as-of, HHG-009's device (null profile) shows 1,001 customers and **33 "fraud customers"** — evidence with zero meaning | Block: `n_known = 0` |
| `n_known < 3` = GENERIC, blocked | **Not supported.** At degree ≤ 20, `n_known < 3` performs as well as `≥ 3` (51.8% vs 49.7%; 24.0% vs 27.2%). The harm comes from degree, not from missing fields | Drop the `n_known` gate except for `NULL`; **degree is the gate** |
| HUB ≥ 100 blocked | **Confirmed** (lift 0.85–0.94) | Block |
| MID 21–100 "allowed only with compound test" | **Confirmed, tighter:** for S02 the lift is ≈ 1.0 → **no sharing evidence at 21–100**; MID devices can enter only through the §1 compound rule | Sharing evidence only at as-of degree **≤ 20**; ≤ 5 strongest |
| Generic email/region ≥ 100 blocked | **Confirmed** (lift ≈ 1.0 at ≥ 300, incoherent at 21–299) | Attribute only |
| Class from a static snapshot | **Insufficient alone.** Snapshot 2016-09-01 → 2016-10-31: 72 of 4,977 devices moved up a class (64 crossed 20; 12 crossed 100, of which 4 jumped from ≤ 20) **and 2,460 devices did not exist at the snapshot** (≈ 49% growth). Regions: 2/280 moved up; R-email 10/60; P-email 8/59. At benchmark time HHG-013's device has 59 customers as-of vs 253 in full data (it would be classed MID vs HUB), HHG-017's 164 vs 299 | Snapshot class is only a pre-filter; **as-of degree is recomputed for every expanded vertex**; unseen entities are handled at query time |

Recommended evidence thresholds after validation: device sharing **≤ 5 = strong, 6–20 = moderate, > 20 = none**; compound ring rule may apply at any degree ≥ 3; regions/emails: attribute only unless degree ≤ 20 *and* supported by ≥ 2 independent fraud cases.

---

## 3. Per-signal validation table

All metrics Aug–Oct PIT; "lift" vs the channel base rate; "cleared" = share of flagged txns that are reviewed false alarms (fraud-vs-cleared discrimination). Ratings reflect **evidence value in a case file**, not triage value.

| ID | Signal (definition) | Historical support | False-positive behaviour | Temporal safety | Rating |
|---|---|---|---|---|---|
| **S01** | Compound device anomaly: cust ≥ 3, new ≥ 0.9, proxy ≥ 0.5, as-of | Ring found 48/48 configs; 96% of ring txns caught after flag; 14-day lead over labels; 0 hits on other 10,780 fraud txns or 384 cleared | ≈ 0 alerts/month after warm-up; 5 warm-up FPs in July; n = 1 ring | Safe (§6) | **HIGH** (for shared-origin/coordination) |
| **S02a** | Device deg ≤ 5 shares with another customer's visible confirmed fraud | precision 49.7–51.8%, lift 4.6–4.8, 263 fraud txns | cleared 0 of 515 flagged | Safe *only* with `closed_at` gating (§6: opened_at gating inflates flagged +29%) | **HIGH** |
| **S02b** | Device deg 6–20, same | precision 24–27%, lift 2.2–2.5 | cleared 6 of 2,549 (0.2%) | same | **MEDIUM** |
| **S02c** | Device deg 21–100 or > 100, same | lift 0.85–1.1 | 39–76 cleared per class | same | **LOW (do not use)** |
| **S03** | Region with recent confirmed fraud (14 d), region deg ≤ 20 | 8 flagged, 3 fraud; higher degrees lift 0.64–1.5 | n too small | Safe with `closed_at` | **LOW** |
| **S04** | `R_emaildomain`/`P_emaildomain` rare (deg ≤ 20), prior fraud 30 d | 36 / 15 txns; 86% / 67% precision | Tiny effective n (few domains) | Safe with `closed_at` | **LOW** (promising, unverified) |
| **S05** | Card had a visible prior confirmed case (customer-level for online) | Recall 83% but precision 4.7% vs 3.8% (lift 1.24); online 11.7% vs 10.6% (lift 1.11); customers with **no** prior case are *less* fraud-prone (7.3%) | Prior case on customer: 47.8% of cleared alerts belong to customers that also have confirmed fraud | Safe with `closed_at` | **LOW** as fraud evidence; keep as context |
| **S06** | R5 sequence: ≥ 3 online < $5 in 1 h on card, then ≥ $20 online | 7 flagged, 2 fraud; **1 of 16** labelled card-testing cases satisfies it. Looser (≥ 1 small then large): 303 flagged, 9.6% (lift 0.9), 8 of 16 cases | 0 cleared | Safe | **MEDIUM** by policy authority (R5 is definitional), **not statistically validated** — treat probability from the policy, not from history |
| **S07** | Burst family: ProductCD C, $400–$500, ≥ 1 prior same-card txn in 1 h | 33 flagged, 15 fraud (45%, lift 4.3), 0 cleared, 18 unlabeled; 5/5 labelled group-2 cases reach ≥ 3 flagged txns (recall 100% of that family) | Unknown for the 18 unlabeled | Safe (window only) | **MEDIUM** — fitted to 5 cases; validate on the Nov–Dec scan only as a "consistent with" check |
| **S08** | Online amount z > 2 vs card history (≥ 20 prior) | 251 flagged, 22.3% (lift 2.1); recall 0.5% | cleared 0.8% (2 txns); 36.7% of undocumented txns qualify | Safe (card-level history) | **MEDIUM** |
| **S09** | Online, new ProductCD for the card (≥ 20 prior) | 374 flagged, 13.9% (lift 1.3) | 0.5% cleared | Safe | **LOW** |
| **S10** | Online `id_15 = New` | precision 7.8% vs base 10.6% (**lift 0.74**), recall 15.9% | 1.27% cleared (98% of cleared alerts are New) | Safe | **LOW** — never cite as evidence of fraud; only as context and only with S01/S02 |
| **S11** | Online anonymous/hidden proxy | 621 flagged, 15.9% (lift 1.5) | 1.45% cleared | Safe | **LOW** |
| **S12** | Online `id_15 = New` **and** proxy | 835 flagged, 11.5% (lift 1.09) | 1.9% cleared | Safe | **LOW** |
| **S13** | Online `addr1` missing | 34,499 flagged, 11.2% (lift 1.06) | — | Safe | **LOW** (descriptive only) |
| **S14** | Online, no identity record | lift 0.51 | — | Safe | **LOW** |
| **S15** | In-person region ≠ card's PIT modal region (≥ 30 prior in-person) | OOR: 87% away vs 55% of non-fraud; ATO only 41%; overall precision 2.47% vs 2.18% (lift 1.13) | **76% of cleared alerts are also away** ("travel"); share-≤ 5% variant lift 1.13, never-seen-region lift **0.81** | Safe if modal region is computed PIT | **LOW** |
| **S16** | Model features (C1/C2/C4/C8/C10, D2/D3/D5, V217/V258/V264, V280/V307/V308) — within-channel AUC vs unlabeled, stable across Jul–Aug and Sep–Oct | Online: C1 0.79, C2 0.76, V258 0.75, V264 0.73, V217 0.72, C4 0.70, D5 0.36 (=0.64), D2 0.38. In-person: D3 0.27 (=0.73), D5 0.28 (=0.72), D2 0.30 (=0.70), V308/V280 0.70, V307 0.68 | Unknown per case | **Unknown** (Vesta engineered features may embed future info) | **LOW** (say "unnamed model feature") |
| **S17** | `risk_score` (triage only) | AUC online 0.78 / in-person 0.88, stable across halves; PIT precision at ≥ 0.7: online 36.2% (lift 3.4), in-person 48.6% (lift 23) | At ≥ 0.81: cleared 15% (online) / 9% (in-person) of flagged; 1 of 30 undocumented txns ≥ 0.5; **0 of the 60** Nov–Dec ring txns ≥ 0.5 | Safe (input) | **LOW as evidence**; strongest single triage variable but circular with how labels were generated, blind to the ring, and contaminated by cleared cases at the top |
| **S18** | Text similarity to closed-case notes | Not statistically validated here; notes are templated (392 templates) and determined by structure | — | Safe with `closed_at` | **LOW** (precedent/context only) |
| **S19** | Customer / step-up response (simulated) | Not testable from data | — | n/a | **HIGH by policy** (R2/R3), simulated |
| **S20** | Hub links (null / > 100 device, ≥ 300 region, top email) | lift ≈ 1 or below | — | Safe but meaningless | **Not evidence** |

### 3.1 Coverage of the labelled data
- No single signal has meaningful recall of the bulk of labelled fraud: S02 (device ≤ 20) recalls 8.3% of non-undocumented confirmed fraud; S01 recalls 0% of it. The dataset's typical fraud (CNP/ATO/OOR single-txn cases) is separable mainly by `risk_score` and unnamed model features, **not** by graph structure.
- The graph earns its place on two classes: **coordinated shared-origin** (S01, S02a/b) and **history-based context** (prior cases, baseline z-scores). This matches the task's "some cases can only be solved by asking what happened on other cards".
- Because the bulk of single-signal cases will be judged under R1 (verify before block), the agent must report a calibrated probability, not a stack of LOW signals.

---

## 4. Card-testing (R5) and policy alignment

| Check | Result |
|---|---|
| Labelled card-testing cases | 16; median 17 txns over 175 h; only 1–6 txns < $5 each |
| Cases satisfying README/R5 exactly (≥ 3 small in 1 h then ≥ $20) | **1 / 16** (CC-2247) |
| Looser (≥ 1 small within 1 h before a ≥ $20 online txn) | 8 / 16 cases, 303 flagged txns, precision 9.6% (no lift) |

Conclusion: the closed history's `card_testing` label is broader than policy R5. The agent must apply R5 as written for policy decisions and must **not** learn "card testing = small-amount sequences" from history.

---

## 5. Evidence hierarchy: revised after validation

| Tier | Evidence | Rating | Notes |
|---|---|---|---|
| 1 | Customer/step-up response | HIGH (policy) | Simulated; recorded in `evidence_requests` |
| 2 | Compound anomalous shared device (S01) + ring expansion | HIGH | Coordination evidence; verify individual customers (R1) |
| 3 | Low-degree device (≤ 5) shared with visible confirmed fraud (S02a); 6–20 = MEDIUM (S02b) | HIGH / MEDIUM | `closed_at` gating mandatory |
| 4 | Policy-defined sequences: exact R5 (S06), burst family (S07) | MEDIUM | Policy authority; sparse history |
| 5 | Amount z-score vs card history (S08) | MEDIUM | |
| 6 | Everything else: repeat victim, region novelty, identity flags, unnamed model features, similar cases | LOW | Context; never sufficient alone; cannot lift P above ~0.5 by stacking |
| 7 | `risk_score` | LOW | Input; never primary |
| — | Hub links | not evidence | Listed as "ignored" |

Changes from the Phase-3 hierarchy: identity anomalies (`id_15`, proxy, "new device") demoted (lift ≤ 1.5, `New` is anti-informative); region novelty demoted; device sharing evidence limited to degree ≤ 20; the ring rule raised because it is the only signal that finds the undocumented shared-device pattern without labels.

**Stacking guard.** Several LOW signals share a cause (e.g., `id_15 = New` and proxy and missing `addr1` are correlated online features). The agent must not count them as independent evidence for the "≥ 2 independent pieces" stopping rule (§6 of the policy); independence should be judged by *source* (device graph, sequence, closed-case history, customer response).

---

## 6. Temporal validation

| # | Test | Result |
|---|---|---|
| T1 | Incremental PIT table vs brute-force filtered-view oracle (device customers, new share, proxy share, visible fraud customers) at 400 random (device, time) points | **400 checked, 0 mismatches** |
| T2 | **Future-corruption invariance:** for 60 (device, as-of) pairs, permute device/customer/identity fields and randomize fraud labels on all rows after `as_of`, and flip the labels of cases that close after `as_of`; recompute | **60 trials, 0 changes** — the function depends only on the visible view |
| T3 | Label-visibility gating: `opened_at` vs `closed_at` (S02, deg ≤ 20, `n_known ≥ 3`) | opened_at gating flags **1,180** txns at 35.5% precision vs **918** at 31.5% with `closed_at`: **+29% volume and +4.0 pts of precision from leaked outcomes**. A random time has **136 cases in flight** (mean; max 191); open→close is 16–142 h (mean 74 h) |
| T4 | Degree measured at end-of-window vs as-of | disagreement on **5.6%** of txns; leaky end-of-Oct degree flags 2,208 vs 3,064 as-of and raises precision from **29.3% → 33.2%** |
| T5 | Snapshot hub class vs as-of class (§2.4) | 2,460 devices (33% of the Oct-31 set, +49% over the snapshot set) unseen at the Sep-1 snapshot; 1.4% of ≤ 20 devices crossed 20; 12 crossed 100; benchmark examples above |
| T6 | Benchmark audit at each of the 20 `opened_at` values: every customer view contains only `epoch ≤ as_of` (`max_ts_in_view ≤ opened_at` for 20/20); visible closed cases have `closed_at ≤ as_of` (20/20; all 20 customers' closed cases are already closed) | Hidden future txns: 1 to 1,224 per customer (HHG-018 1,224; HHG-007 356; HHG-003 154) |
| T7 | Device stats at each flagged device, as-of vs full data | Ring device (HHG-014): 44 vs 52 customers, new 1.00, proxy 1.00, 4 fraud customers (both) → the rule is stable but the customer count would be overstated with future data. HHG-013: 59 vs 253; HHG-017: 164 vs 299; HHG-006: 494 vs 543; HHG-009 (null profile): 1,001 customers, 33 "fraud customers" |

**Other leakage found during validation.**
1. Findings §2.1 "home region" used the whole-period mode → replaced by PIT modal region (§3, S15).
2. Findings §2.4–2.5 used all-channel base rates; the corrected within-channel figures are in §0.1.
3. The first version of the ring analysis used final (Oct 31) customer counts "at flag"; the PIT count at the first flag was 3 customers.
4. `risk_score` is high on cleared alerts by construction; using "closed case exists" as a proxy for fraud (S05) inflates recall but adds no lift.

**The evidence hierarchy contains no future information provided that:** (a) every device/region/email aggregate is computed as-of; (b) closed cases and their labels are gated by `closed_at`; (c) Case-memory uses the case's own `as_of`; (d) unnamed V/C/D features are treated as opaque and never as verified PIT signals (their temporal safety is *unknown*, hence LOW). T1/T2 supply an oracle for Phase 5 tests.

---

## 7. Decisions handed to Phase 5 (graph implementation)

1. **Ring rule:** `cust ≥ 3, new_share ≥ 0.9, proxy_share ≥ 0.5`, as-of, no `n_known` gate; suppress alerts during the first 30 days of graph history. Parameterised; report the parameters in the evidence.
2. **Hub gating by as-of degree:** `NULL` blocked; sharing evidence only at degree ≤ 20 (≤ 5 strong); > 20 = none; snapshot class as pre-filter only.
3. **Label visibility:** `closed_epoch ≤ as_of` (not `opened_at`).
4. **Drop from the evidence path:** V93/V52/V79, `id_15 = New`, proxy, `addr1` missing, region "never seen"; keep as returned context fields.
5. **Keep** `risk_score` and unnamed features as context; never in a filter.
6. **R5** implemented as written; do not infer card testing from history.
7. **Acceptance tests to reuse:** T1 (oracle equality), T2 (future-corruption invariance), T6 (benchmark audit), plus "ring flagged at as-of 2016-08-16 with 3 customers and 14 days before the first label".
8. **Unresolved:** (a) only one historical ring → treat S01 as a discovery aid, not a probability source; (b) the burst family (S07) is fitted to 5 cases; (c) 18 unlabeled hits of S07 and 42 unlabeled ring txns are unverifiable; (d) V/C/D temporal provenance is unknown; (e) the similar-case retrieval (S18) is untested; (f) `n_known` semantics only matter for the ring's displayed profile string.

*Reproducibility note:* analyses were run in the session scratchpad with pandas/scipy against the original CSVs (intermediate pickles from earlier phases); they are not saved in the repository. Random seeds: 3 (T1 sample), 7/11 (T2), 0 (baseline sample).
