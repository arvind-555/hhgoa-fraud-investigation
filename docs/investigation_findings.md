# Investigation findings — Phase 2 (data investigation)

All statistics were computed programmatically (pandas) over the full files; nothing was hand-read from `transactions.csv`. Labelled history = `closed_cases_history.csv` (Jul 2 – Oct 31 transactions). "Baseline" = transactions with no closed case; it is **unlabeled, not verified clean** (it may contain undetected fraud). No benchmark case has been given a verdict here.

Confidence scale: **High** = deterministic or ≥99% reproducible on all labelled evidence; **Medium** = strong statistical lift, exceptions exist; **Low** = suggestive.

---

## 1. Card identity mapping — RESOLVED (High)

`transactions.csv` has no `card_id`; case IDs look like `C12382-K1`.

**Rule that reproduces every known card ID:**
`card_id = customer_id + "-K" + n`, where *n* = the rank (starting at 1) of the transaction's `card6` value among the distinct `card6` values seen for that customer, sorted ascending with missing (`NaN`) first — i.e. `'' < 'charge card' < 'credit' < 'debit' < 'debit or credit'`.

| Check | Result |
|---|---|
| Closed-case transactions whose derived card = case `card_id` | **14,955 / 14,955** (100%) |
| Closed cases whose transactions all map to their `card_id` | **5,565 / 5,565** |
| Case-pack flagged transactions → case-pack `card_id` | **20 / 20** |
| Cases needing K ≥ 2 (discriminating test; naive "always K1" fails) | 2,585 cases, all correct |
| Brute-force search over all 30 field subsets × 4 sort orders | Only `card6` and (`card4`,`card6`) reach 100%; `card4` never varies within a customer, so they are equivalent. First-seen, frequency, `card2`, `card3`, `card5` orderings all ≤ 99.2% and none discriminate |
| Cross-customer stability | Rule derived on evidence customers only, applied to all 13,553 |

Facts: 14,317 derived cards (12,793 customers have 1, 756 have 2, 4 have 3). Distinct `(card2,3,4,5,6)` combos number 14,893, so ~576 combos are *not* separate cards (card2/card5 vary within a card). Earlier hypothesis "card = card2..6 combo" is **wrong** (41 case cards span 2 combos, 1 spans 3).

Caveats (do not hide them):
- A customer's **null-`card6` transactions form their own card, K1** (1,571 txns; 672 customers), and the customer's real card becomes K2. Confirmed in cases (e.g. C01119-K1 is a null-type card; K2 holds 3,546 txns). These are probably incomplete-record rows; treat K1-null as "unknown-type card" bucket.
- K3/K4 (rare `card6` values) verified on only 7 cases / 9 txns.
- The rank uses the customer's full history including transactions after `opened_at`. This is a naming convention fixed by the dataset builder, not evidence; it does not leak fraud information.
- `card_id` is an identifier-only key. It cannot be used to infer fraud.

**Recommendation:** store `card_id` as the derived key; also store the raw `card2..card6` as properties; keep `card1` = customer.

---

## 2. Fraud pattern discovery (interpretable signals)

Population: Jul–Oct, 14,055 confirmed-fraud txns (5 patterns + 30 undocumented), 900 cleared alert txns, 312,913 baseline txns from customers that have cases. History features use only earlier transactions of the same customer.

### 2.1 What the labels look like (structure of the generator)

| Fact | Evidence |
|---|---|
| Pattern is a **case-level** label with deterministic channel structure | CNP, CNP-new-device, card testing, undocumented: 100% online. Out-of-region: 100% in-person (`ProductCD` W). Account takeover: 975 cases in-person only, 230 mixed (79% of ATO txns are W) |
| **New-device rule (High):** a case is `card_not_present_new_device` **iff** at least one of its online txns has `id_15 = New`; a `card_not_present_fraud` case has **zero** `id_15 = New` txns | 1,076/1,076 new-device cases have any-New; 0/1,404 CNP cases do; only 51% of new-device cases have *all* txns New |
| Out-of-region txns are in the customer's country (`addr2 = 87`), addr1 differs from the customer's modal region in **89.3%** (baseline in-person: 47.4%) | `dist1` present 51% |
| `card_testing` cases are long: median 17 txns over 175 h, 1–6 txns < $5. Policy R5 ("≥3 small auths within an hour then a larger purchase") is matched by ≤ 1–2 of 16 labelled cases. **The history is looser than the policy** | Only CC-0370 (6 small in 2 h) resembles R5 |
| Cases are small: median 1 txn (CNP, OOR), 2 (ATO, new device); p90 span 17–74 h | |
| Repeat victims: 753/1,892 customers have ≥2 closed cases (max 27); 79.5% of cases belong to repeat customers; 47.8% of repeat cases repeat the prior pattern | |

### 2.2 Confirmed fraud vs baseline vs cleared (rates are % of that group's txns)

| Signal | Baseline (case customers) | Confirmed fraud | Cleared | Undocumented (30) |
|---|---|---|---|---|
| Online | 28.2 | CNP 100 / new-dev 100 / OOR 0 / ATO 21 | 84.8 | 100 |
| `risk_score` > 0.7 | 2.4 | ~25 (all patterns) | **100** (min 0.81, mean 0.88) | 0 (mean **0.14**) |
| Median amount ($) | 68 | CNP 55, new-dev 48, OOR 117, ATO 117 | 100 | 468 |
| First-seen device profile for customer | 11.4 | CNP 43, new-dev 30, ATO 5 | **65** | 20 |
| `id_15 = New` (of identity rows) | 45.6 | new-dev 40, ATO 47, CNP 0 | **98** | 63 |
| Proxy flag (`id_23` set) | 3.5 | 4–7 | 8 | **37** |
| `addr1` missing | 11.9 | CNP 61, new-dev 68, testing 94, ATO 5 | 8 | 7 |
| New product code for customer | 0.8 | CNP 4.7, new-dev 2.2 | 14 | 27 |
| Amount above customer's prior max | 1.6 | 3–7 | 17 | 17 |
| Foreign billing country (`addr2≠87`) | 0.8 | CNP/new-dev 5, testing 6 | 3 | 0 |
| Customer history < 5 prior txns | 2.3 | CNP 15.6 | **30** | 0 |
| ≥1 prior txn on the card within 1 h | 51 | CNP 30 | 17 | 50 |
| M1–M9 all missing | ~54 | 73 (M4 present 73) | 89 | 100 |
| Night hours 00–05 | 25 | 20–33 | **0** | 32 |

Interpretation (Medium): the score is informative for fraud overall (fraud rate 0.5% at ≤0.1 rising monotonically to 41% at >0.9), **but** every one of the 900 cleared alerts scored ≥ 0.81, so above 0.81 the labelled history mixes 1,866 confirmed-fraud txns, 900 cleared, and 2,811 never-reviewed txns. Among reviewed high-score alerts fraud share is 67.5%. Undocumented-pattern fraud hides at ~0.14.

### 2.3 Cleared cases (900)

Single transaction, exposure 0, three templated reasons:

| Reason in notes | n | Online | new addr1 | new device | id_15 New | Median $ | Notes |
|---|---|---|---|---|---|---|---|
| "travel to the billing region" | 716 | 84% | 50% | 70% | 82% | 100 | Template is weakly tied to the data (84% online) — do not treat the note text as a checkable fact |
| "new phone, device added" | 158 | 100% | 2% | 54% | 100% | 50 | Confirms that *New device alone* is not proof |
| "amount unusual but consistent with intent" | 26 | 4% | 0% | 4% | 4% | $990 | z = 2.7 vs history; in-person large purchase |

Cleared cases: 47.8% belong to customers that also have confirmed fraud (so a cleared alert does not immunise a customer).

### 2.4 Candidate rules (Jul–Oct; positives = 14,055 labelled fraud; base rate 2.4%; unlabeled counted negative)

| Rule (all use only prior history) | Flagged | Fraud | Precision | Recall | Lift |
|---|---|---|---|---|---|
| `risk_score ≥ 0.9` | 1,289 | 523 | 40.6% | 3.7% | 12.0 |
| in-person & `risk ≥ 0.5` | 8,235 | 2,853 | 34.6% | 20.3% | 10.3 |
| `risk ≥ 0.7` | 13,418 | 3,708 | 27.6% | 26.4% | 8.2 |
| online & amount z > 2 vs history (≥20 prior txns) | 481 | 76 | 15.8% | 0.5% | 4.7 |
| online & `addr1` missing | 47,697 | 4,895 | 10.3% | 34.8% | 3.0 |
| online & anonymous/hidden proxy | 1,380 | 127 | 9.2% | 0.9% | 2.7 |
| online & ≥1 prior sub-$5 online auth in last hour | 885 | 82 | 9.3% | 0.6% | 2.8 |
| online & ≥3 txns in 48 h & amount < $100 | 62,184 | 4,356 | 7.0% | 31.0% | 2.1 |
| online & new device profile (≥20 prior txns) | 32,588 | 1,927 | 5.9% | 13.7% | 1.8 |
| online & `id_15 = New` | 48,794 | 2,218 | 4.5% | 15.8% | 1.3 |
| in-person & billing region never seen (≥20 prior txns) | 6,200 | 106 | 1.7% | 0.8% | **0.5** |

Takeaways: (a) **no single behavioural rule is strong**; `id_15 = New`, new-device, and new-region (in-person) are *weak or anti*-informative on their own (many legit customers; 98% of cleared alerts are New). (b) Signals become useful **in combination and with the connection features of §5**. (c) "New region" for in-person is worse than baseline — the OOR signal is *region ≠ home region* (89% vs 47%), not "never seen".

### 2.5 C / D / M / V columns (univariate AUC, fraud vs baseline; interpretable, not a classifier)

| Group | Finding | Use as |
|---|---|---|
| `V51,52,57,58,79,81,92,93,94` | AUC 0.91–0.93 for CNP and CNP-new-device (online); block V35–V94 missing 13–29% | "Vesta feature X is high" — cite by name, say unnamed |
| `V217–V219, V257–V265` | AUC 0.71–0.74 across all fraud vs baseline | Weak general fraud indicator |
| `V307,V308,V280` | 0.66–0.69 for OOR/ATO | |
| `D2, D3, D5, D7, D8` | AUC 0.26–0.35 (**lower in fraud**: short time since previous event) ; D5/D7 highest in cleared (AUC 0.79–0.90 cleared-vs-fraud) → cleared cases are *older*, established relationships | "Days-since" delta low → suspicious |
| `C1, C2` | lower in cleared than fraud (AUC 0.22–0.24) | |
| `C9` | AUC 0.18 in CNP (low) | |
| `M4` | present in 73% of fraud vs 27% missing; `M2/M3` T-rate falls in fraud | Missing/false match flags |
| `M1–M9` all missing | 89% of cleared, 100% of undocumented, 73% of fraud | |
| Undocumented: `C4, C8, C10` AUC 0.89; `V302–V304` 0.87; `D`s null | Seed signature (see §7) | |

All are used **only as named signals with an honest label** ("model feature V93, meaning unknown").

---

## 3. Benchmark case analysis (evidence available at `opened_at` only)

**Guard used:** every query filters `ts ≤ case.opened_at` (in the case pack `opened_at` is 1–6 h after the flagged txn, so later activity in that window *is* legitimately visible). Counts of rows excluded as future are given per case. No verdicts are given; this is the evidence table an agent would see.

Abbreviations: `hist` = txns of this card before the flagged one; `home` = card's modal `addr1`; `pctile` = flagged amount vs the card's earlier amounts; `prodshare` = share of card's earlier txns with the same ProductCD; `dev-shared` = other customers seen on the same device profile up to `opened_at` (in parentheses: how many with labelled fraud / in last 30 d); `future` = same-customer txns after `opened_at` excluded.

| Case | Trigger | Flagged txn (ts, channel, $, score) | Card history | Evidence in graph at opened_at | Prior closed cases (customer) | future excluded |
|---|---|---|---|---|---|---|
| HHG-001 | risk 0.61 | 12-04 19:55 in-person W $77.07 | 358 txns / 151 d | Region 444 vs home 204; card used region 444 in 2.8% of history; 9 txns in 48 h; 1 later txn ($38.92) before open | 4 confirmed (OOR, CNP-new-device) | 62 |
| HHG-002 | risk 0.79 | 11-22 17:27 online C $292.36 | 35 / 142 d | Largest amount ever on card (z 2.4); no identity record; no addr1; prodshare 1.0 | 1 confirmed (CNP) | 8 |
| HHG-003 | report ("never made $49.00") | 12-10 13:01 in-person W $49.00, score 0.40 | 980 / 157 d | Region 330 vs home 299 (4.3% of history); 13 txns in 48 h; low amount (28th pctile) | 6 (5 fraud: OOR, ATO) | 154 |
| HHG-004 | report ($128.33) | 12-29 01:53 online C, score 0.34 | 209 / 175 d | `id_15` New, device profile first seen (weak profile `? \| ? \| firefox 47.0`); 99th pctile amount; addr1 missing | 4 (3 fraud CNP) | 6 |
| HHG-005 | risk 0.54 | 12-07 21:38 online R $100.07 | 90 / 158 d | ProductCD R = 6.7% of history; `id_15` New; new device; region 330 = home (94%); iOS device shared by 109 customers (1 with fraud) | 3 (ATO) | 1 |
| HHG-006 | report ($482.12) | 11-21 20:30 online C, score 0.25 | 199 / 139 d | **Fourth of four C purchases in 30 min** (20:00 $478.95, 20:10 $456.96, 20:24 $488.04, 20:30 $482.12 = $1,906.07), two device profiles alternating; C = 1.5% of history; **minute-resolution timestamp (seed marker)**; 3 txns within the hour; region 264 = home | none | 60 |
| HHG-007 | risk 0.87 | 12-05 00:46 in-person W $111.92 | 2,431 / 155 d | Region 264 = home (91%); 3 more txns to open ($271); mega-customer; score ≥ 0.81 (see §2.2) | 19 (18 fraud: OOR, ATO) | 356 |
| HHG-008 | report ($55.68) | 12-19 22:08 online C, score 0.38 | 899 / 171 d | Device (chrome 66) used 9× earlier by this customer; `id_15` Found; 14 txns in 48 h | 19 (18 fraud) | 27 |
| HHG-009 | report ($30.02) | 12-28 12:10 online S, score 0.28 | 46 / 177 d | Region 203 vs home 330 (2.2%); device = null hub profile (ignore); `id_15` Found | none | 9 |
| HHG-010 | risk 0.90 | 12-02 15:18 online R $1,000.03 | 33 / 149 d | Largest ever (z 2.5); `id_15` New, new device; region 469 = home; device shared by 184 customers (10 with fraud, 36 in 30 d); anonymous.com email; score ≥ 0.81 | 1 (**cleared**) | 2 |
| HHG-011 | report ($131.30) | 12-29 03:27 online C, score 0.39 | 10,306 / 180 d | Mega-customer (60 txns in 48 h + 5 more to open); `id_15` New; Android device shared by 2 | 11 (10 fraud incl. card testing) | 20 |
| HHG-012 | risk 0.55 | 12-18 04:00 in-person W $30.91 | 910 / 169 d | Region 494 vs home 325 (2.3%); 13 txns 48 h | 2 (1 fraud CNP, 1 cleared) | 77 |
| HHG-013 | risk 0.76 | 12-09 02:39 online C $35.66 | 1,431 / 159 d | ProductCD C = 0.1% of history; `id_15` New; addr1 missing; device shared by 58 | 4 (3 fraud) | 128 |
| HHG-014 | analyst | 11-22 16:11 online C $74.96, score **0.05** | 71 / 141 d | **Device = suspicious shared profile** (Samsung SM-G935F, Android 7.0, Chrome 62, 1920×1080, anonymous proxy, `id_15` New); same profile on this card 11-15 ($112.37); profile seen on 43 other customers up to opened_at (4 with labelled fraud, 19 in last 30 d); minute-resolution timestamp; C = 1.4% of history | none | 12 |
| HHG-015 | risk 0.77 | 11-17 14:03 online R $599.94 | 68 / 138 d | Largest ever (z 1.9); region 327 vs home 325 (never used); `id_15` New; anonymous.com email | 3 (2 fraud) | 10 |
| HHG-016 | report ($59.67) | 12-11 22:39 online C, score 0.37 | 48 / 158 d | `id_15` New, new device shared by 142 (8 with fraud; 51 in 30 d); addr1 missing | none | 12 |
| HHG-017 | risk 0.57 | 11-11 23:46 online R $100.09 | 52 / 117 d | `id_15` Found; **HIDDEN proxy**; Windows-10/Chrome-65 device shared by 163 customers (all in 30 d); region 204 vs home 325; 8 cards with confirmed fraud in region 204 in prior 14 d | 1 (**cleared**) | 6 |
| HHG-018 | report ($39.08) | 11-27 13:41 in-person W, score 0.48 | 5,860 / 149 d | Region 126 vs home 325 (9.6%); **2 later txns totalling $724.87 before opened_at**; 97 txns in 48 h (mega-customer) | 20 (19 fraud) | 1,224 |
| HHG-019 | risk 0.90 | 12-01 17:28 online R $99.92 | 219 / 152 d | R = 5.9% of history; `id_15` New; region 264 vs home 325 (2.3%); device shared by 2 (no fraud); score ≥ 0.81 | 4 (3 fraud) | 27 |
| HHG-020 | risk 0.52 | 12-03 06:04 online R $125.08 | 82 / 150 d | **First-ever R purchase**; region 264 = home (100%); `id_15` New; device shared by 231 (43 in 30 d) | 2 (ATO) | 28 |

Notes for the agent design:
- Customers with **no** prior cases: HHG-006, 009, 014, 016. Customers whose only prior case is **cleared**: HHG-010, 017.
- Flagged txns with **minute-resolution timestamps** (seed marker): HHG-006, HHG-014 only.
- Mega-customers (1,000+ txns): HHG-007, 011, 018 (plus 003, 008, 012, 013 at 900–1,500) — behavioural baselines need card-level granularity.
- For customer-report cases the trigger hides the score; it is present on the transaction (0.25–0.48).
- The `future` column shows how much of the graph an unguarded agent would see (up to 1,224 txns for one case). See §4.

---

## 4. Temporal leakage

**Enforced rule:** `txn.ts ≤ case.opened_at` for every read used as evidence; closed-case memory additionally requires `closed_at ≤ case.opened_at` (holds for all 20: latest `closed_at` is 2016-11-06, earliest `opened_at` is 2016-11-12).

Other leakage sources found or to guard against:

| # | Source | Severity | Handling |
|---|---|---|---|
| 1 | Transactions after `opened_at` (same card/customer/device/region) | High | Filter every traversal by `ts`; graph queries take `as_of` parameter |
| 2 | Aggregated properties precomputed over the whole graph (device degree, customer counts, region degree, baselines) | High | Compute at query time with `as_of`, or store timestamped counters |
| 3 | **`risk_score` on later transactions** and `risk_score` of the flagged one | Medium | Input only; never a label |
| 4 | Vesta **V/C/D features** are "ranking, counting, relationships between entities" possibly computed over the full public dataset (incl. later txns) | Medium (unknowable) | Use as signals only; state in evidence they are model-supplied |
| 5 | **Seed-row artifacts**: minute-resolution `ts`, constant C-template (see §7) reveal *how* a row was made, not whether the bank could know at `opened_at` | Medium | Do not use as evidence in a case file; use for offline benchmarking of the pipeline only |
| 6 | Card K-index built from full-history `card6` values | Low | Identifier only |
| 7 | Closed-case fields: `exposure_usd`, `txn_ids`, `connected_card_ids` list *future* txns/cards of *that* (closed, historical) case only | Low | Safe to retrieve (closed before exam) but never "leak" into current case as its own affected list |
| 8 | Unlabeled Nov–Dec fraud cannot be used as ground truth; unlabeled Jul–Oct is not "legitimate" | Medium | Do not train/threshold on "unlabeled = legit"; treat 2,811 unreviewed ≥0.81 alerts as unknown |
| 9 | Trigger text embeds the model score; ordering by case number has no meaning | Low | |
| 10 | Case-pack `opened_at` is *after* the flagged txn; strict `ts ≤ flagged.ts` is stricter than required, `≤ opened_at` is the true information boundary | Info | Use `opened_at` |

---

## 5. Device / connection analysis

### 5.1 The shared suspicious profile (verified in Aug–Sep AND Nov–Dec)

Profile **`SM-G935F Build/NRD90M | Android 7.0 | chrome 62.0 for android | 1920x1080`** (identity `DeviceInfo` also appears as `SAMSUNG SM-G935F Build/NRD90M` in 88 other rows; the labelled profile string is the one above):

| Fact | Value |
|---|---|
| Transactions | **114** across **52 customers** (12 customers with 1 txn, 18 with 2, 22 with 3) |
| Waves | Aug 44 txns/22 cust; Sep 10/8; **Nov 50/28; Dec 10/10** — two waves with **zero customer overlap** (24 Aug–Sep, 28 Nov–Dec) |
| Always | anonymous proxy (`IP_PROXY:ANONYMOUS`), `id_15 = New`, ProductCD C, minute-resolution timestamp |
| Amounts | $35.70–$258.29; median ~$149 |
| Risk scores | max 0.44, mean **0.14** (none would trigger a risk-score alert) |
| Timing | business hours 08–22 h; customer's txns ~5 days apart; period 2016-11-14 → 12-04 in second wave |
| Labelled | 10 txns in 4 closed `undocumented` cases (CC-2649/2971/2985/3035); the other 104 are **unlabeled** — 44 of them in Nov–Dec, i.e. not visible in closed history |
| Wider proxy footprint | Anonymous proxy is used by 452 customers overall (1,185 txns) — proxy alone is not the ring |
| As-of HHG-014 `opened_at` (2016-11-22 20:11) | already **80 txns / 44 customers** (20 customers in prior 30 d) |
| Connection to benchmark | **HHG-014's card C13487-K1** has three ring txns (11-15, 11-22 flagged, 11-26 future). No other case-pack customer uses this profile |

The closed-case field `connected_card_ids` for CC-2649/2971/2985/3035 lists 23–24 cards; these were not validated against the 114-txn set here (unresolved U4).

### 5.2 A second, different suspicious cluster (rotating devices, high amounts)

Cases CC-3748/3841/3907/4086/4124 (all `undocumented`, ~$1.9k each, 4 txns in ~30 min on one card, ProductCD C, amounts $458–$499, **each txn from a different device profile**, `D1 = 0`, constant C-vector, minute-resolution timestamps, no `connected_card_ids`). Matching bursts exist beyond the labelled 5 (12 customers found by fingerprint, incl. 3 unlabeled in Sep, and in Nov–Dec: C03633 (11-25), C10751 (11-24), C12641 (11-29), C05766 (12-03), C06881 (12-03), and **C07297 (11-21 = HHG-006)**). Distinct from 5.1: no shared device; the shared element is the *behavioural fingerprint* (amount band, product C, burst, rotating devices).

### 5.3 Does sharing predict fraud? (propagation test)

Share of transactions where **another customer had labelled fraud on the same entity in the previous 30 days** (fraud txns vs 60k random baseline txns; Jul–Oct):

| Entity | Degree (customers sharing it) | Fraud txns | Baseline txns | Reading |
|---|---|---|---|---|
| Device profile | 2 | 26.6% | 0.9% | Strong (small n) |
| | 3–5 | 50.6% | 3.1% | Strong |
| | 6–20 | 71.0% | 13.4% | Strong |
| | 21–100 | 79.5% | 28.6% | Moderate |
| | 101–300 | 89.5% | 66.3% | **Weak — hub** |
| Billing region (`addr1`) | 6–20 | 80.3% | 15.1% | Strong |
| | 21–100 | 83.5% | 73.4% | Weak |
| | 101–3,000 | 90.3% | 83.9% | Meaningless |
| `R_emaildomain` | 21–300 | 80–96% | 35–52% | Weak |
| `P_emaildomain` | 21–9,000 | 73–92% | 39–61% | Weak |

(Caveat: fraud is clustered on the same customers, so lifts are optimistic; the *ordering by degree* is the reliable result.)

---

## 6. False-hub detection (practical rules)

| Entity | Rule | Basis |
|---|---|---|
| Device profile | Ignore any profile where `DeviceInfo`, `id_30`, `id_31`, `id_33` are all null (`? \| ? \| ? \| ?` = 832 customers Jul–Oct; 1,011 overall) | Degree; no information |
| Device profile | Down-weight when < 3 of the 4 components are known (e.g. `? \| ? \| mobile safari generic \| ?` 550 customers, `Windows \| ? \| chrome 63.0 \| ?` 623) | Generic browser strings |
| Device profile | Hub if degree ≥ 100 customers (89 profiles); weak if 21–100; **strong if ≤ 20** (baseline overlap falls from 66% to ≤ 13%). Measure degree **as of `opened_at`** | §5.3 |
| Device profile | Named phone models with `Build/…` strings are most informative (few customers per profile) | Ring profile degree 52 overall, ≤ 3 per customer |
| `addr1` | 38 regions have ≥ 300 customers (299, 204, 264, 325, 330, 315 top; 1.2k–1.8k customers); regions with degree > 100 are not a connection; use region only as *home vs not home* and for rare-region clusters | §5.3 |
| `addr2` | Ignore (87 = 88.1% of rows); use only non-87 as an anomaly | |
| Email domain | Ignore top-N (gmail 7,871, yahoo 5,043, anonymous.com 4,109, hotmail 2,893, aol 2,341, comcast 1,146 customers); only 16 of 59 P-domains have < 50 customers; treat domain as an attribute, not a link. `anonymous.com` is a *feature* (privacy service) not a shared origin | Degree |
| `ProductCD` | Attribute only; W ⇔ in-person | |
| Mega-customers | `customer_id` with > 1,000 txns is an aggregate; baselines and "new X" logic must be per **card** and prefer recent window | 14,932 max |
| Timestamps | Ignore ties/same-second duplicates (157 same-customer same-second pairs) | |

---

## 7. Seeded data

README: "a small number of rows were added to seed investigation exercises."

**Identified with high confidence (High):**

| Marker | Evidence |
|---|---|
| **Minute-resolution timestamp** (`TransactionDT mod 60 = 0`) | 30/30 undocumented-case txns and 114/114 ring txns. Natural rate is 1.65% (9,721 rows), spread uniformly across labels, so the marker alone has no discriminating power; it is discriminating only in combination |
| Ring profile txns (§5.1): 114 rows, 100% minute-resolution, C, anonymous proxy, New | 44 rows in Nov–Dec are **unlabeled** = the seeded exam material |
| Burst fingerprint (§5.2): ProductCD C, amounts 440–500 ($458–$499 in labelled), ≥ 2–4 txns within ~30 min, rotating devices, `D1 = 0`, other D null, M all null, C-vector `[1,1,0,1,0,1,0,1,0,1,1,0,1,1]` | 12 customers found |
| Benchmark flagged txns that carry the marker | **HHG-006** (burst), **HHG-014** (ring) |

**Not identifiable:** other seeded rows for card-testing, CNP, OOR, ATO or cleared exam cases — none of these patterns show the minute-resolution marker above base rate (1.3–2.1% vs 1.65%), so those exam transactions are either original Vesta rows or seeded without a marker. C-vector templates alone are not a discriminator (34,898 rows share it; only 641 minute-resolution).

**Effect on investigation logic:**
- The ring (§5.1) is only discoverable through **cross-customer graph traversal**, not risk score (mean 0.14).
- Do **not** ship "sec==0" as an agent evidence rule (leaks the data-generation method, and the benchmark key would not reward it). Use it in offline evaluation only, to find the ring and burst rows as a test set.
- Because seeded rows are placed in Nov–Dec without labels, Nov–Dec cannot be used to *validate* rules with labels — only the Jul–Oct history can.
- Undocumented-pattern rule R9 ("coordinated or repeated abuse across customers") maps to §5.1 (shared device) and possibly §5.2 (shared fingerprint).

---

## 8. Prior-case memory (`closed_cases_history.csv`)

| Field | Keep as | Retrieval use |
|---|---|---|
| `case_id` | ClosedCase key | Cited in `similar_prior_cases` |
| `customer_id`, `card_id` | edges to Customer/Card | Repeat-victim lookup (79.5% of cases; 753 customers ≥ 2 cases) |
| `pattern`, `outcome` | properties | Label; filter |
| `opened_at`, `closed_at` | properties | Enforce `closed_at ≤ as_of` |
| `txn_ids`, `first_fraud_txn_id`, `n_txns`, `exposure_usd` | edges/properties | Derive device profile/region/email/amount of each historical case; exposure for cases > $2,500 typical sizes; cleared cases have exposure 0 |
| `actions_taken`, `report_filed` | properties | Only 3 action sets; SAR filed in 397 cases (14 CNP, 56 new-device, 124 OOR, 187 ATO, 7 testing, 9 undocumented) — useful as a base rate of "when SARs were filed" |
| `connected_card_ids` | edges (`CONNECTED_TO`) | 4 cases only, 23–24 cards each |
| `analyst_notes` | text embedded for vector search | 392 distinct normalised templates (CNP 174, new-device 162, ATO 45, testing 5, cleared 3, OOR 1, undocumented 2). 38.9% of notes embed a **device description** (CNP 73%, new-device 85%, testing 62%, ATO 17%, OOR 0%) — extract into structured `device_text` |

Additional derived memory features (recommend computing at load): per case — the set of device profiles (with degree), regions, email domains, amount band, channel mix, product codes, time span, whether the case involves a low-degree (≤ 20) device also seen in another case (**36%** of cases; 94% share a device with another case when hubs are included, so degree filtering is essential).

Cautions: notes are templated and partly inconsistent with data (e.g. "travel" cleared cases are 84% online); patterns are known deterministically from structure (§2.1), so pattern text is a label, not independent evidence; Nov–Dec has no memory.

---

## 9. Summary lists

### Confirmed findings
1. `card_id = customer_id + K(rank of card6, NaN first)` — 100% on 14,955 case txns, 5,565 cases, 20/20 benchmark cards.
2. Pattern labels are structural: new-device ⇔ any online txn with `id_15 = New`; CNP ⇔ none; OOR ⇔ in-person, region ≠ home (89%); ATO mixed channel, mostly W; card-testing cases are long and only rarely match policy R5's 1-hour rule.
3. `risk_score` is monotonic with fraud overall but every cleared alert is ≥ 0.81; undocumented fraud averages 0.14.
4. No single behavioural feature is strong; `id_15 = New` is 98% of cleared alerts. Combination with cross-entity sharing is the signal.
5. Shared low-degree device profile (≤ 20 customers) with another customer's labelled fraud in the prior 30 d is by far the strongest connection signal (26–71% vs 1–13%); hubs (≥ 100) are noise.
6. The suspicious profile `SM-G935F … chrome 62 … 1920x1080` (anonymous proxy, New, ProductCD C, minute-resolution timestamps) has 114 txns / 52 customers in two waves; the Nov–Dec wave (60 txns, 28 customers) is unlabeled and already 80 txns / 44 customers when HHG-014 opens.
7. HHG-014's card uses the ring device; HHG-006 sits inside a four-transaction rotating-device burst ($1,906.07 in 30 min); these two flagged txns carry the seed marker.
8. Seeded rows are only identifiable for the undocumented-type material; not for other patterns.
9. False hubs: all-null device profile, ≥ 100-customer device profiles, regions with > 100 customers, all top email domains, `addr2 = 87`.
10. Temporal guard verified: 20/20 cases have closed cases fully before `opened_at`; up to 1,224 future txns per case would otherwise leak.

### Unresolved findings
- **U1** How the graph should treat K1 = null-`card6` card (672 customers): merge into K2? (data says separate card).
- **U2** Whether `connected_card_ids` in the 4 ring cases match the 114-txn ring (52 customers). Not cross-checked.
- **U3** Whether the Sept unlabeled bursts (C05284, C05698, C13354) and Nov–Dec bursts are the same seeded family; the fingerprint is heuristic (12 found).
- **U4** Which V/C/D columns are computed with future information (unknowable); ATO and OOR have only weak column-level signals (AUC ≤ 0.69).
- **U5** The right "home region" definition for mega-customers; modal `addr1` over the card's history is a working definition (89% vs 47%).
- **U6** Why 2,811 ≥ 0.81 alerts in Jul–Oct were never closed-case reviewed; the exam's high-score cases (HHG-007, 010, 019) sit in this ambiguous zone.
- **U7** Whether the card-testing definition in the exam follows policy R5 (1-hour sequence) or the looser history; no flagged exam case was checked for it.
- **U8** Whether card-level "new device / new product / new region" flags are better than customer-level for multi-card customers (I used customer-level).
- **U9** The regulatory PDFs are links only (not loaded).
- **U10** Prior-cases similarity (which closed cases the key expects in `similar_prior_cases`) is unknown.

### Recommended graph entities
`Customer`, `Card`, `Transaction`, `DeviceProfile` (with a `quality` field: full / partial / null), `EmailDomain`, `BillingRegion`, `ClosedCase`, `Case` (agent-written), `CaseNote`/`Document` chunk (vector), optional `ProductCode`.

### Recommended graph edges
`Customer –OWNS→ Card`; `Card –MADE→ Transaction`; `Transaction –NEXT→ Transaction` (per card, by `ts`); `Transaction –FROM_DEVICE→ DeviceProfile`; `Transaction –PURCHASER_EMAIL→ / –RECIPIENT_EMAIL→ EmailDomain`; `Transaction –BILLED_IN→ BillingRegion`; `ClosedCase –ON_CARD→ Card`; `ClosedCase –INVOLVES→ Transaction`; `ClosedCase –CONNECTED_TO→ Card`; `Case –INVESTIGATES→ Transaction`; `Case –ON_CARD→ Card`; `Case –SIMILAR_TO→ ClosedCase`; `Case –CITES_DEVICE→ DeviceProfile`; `DeviceProfile –SEEN_ON→ Card` (with `first_seen`, `last_seen`, `n_txns` for fast time-bounded sharing).

### Recommended graph properties
- **Transaction:** `ts`, `amount`, `product_cd`, `channel`, `risk_score`, `id_15`, `proxy` (`id_23`), `device_type`, `addr1`, `addr2`, `dist1`, `dist2`, `has_identity`, `card6`, selected `C1,C2,C4,C8,C10`, `D1–D5`, `M1–M9` (as strings), a handful of named V (`V51,V52,V79,V93,V94,V217,V258,V264,V308`), plus derived `sec_resolution` for offline use only.
- **Card:** `card_id`, `customer_id`, `card4`, `card6`, `card2`, `card3`, `card5`, `first_ts`, `last_ts`, `is_null_type`.
- **Customer:** `customer_id`, `n_cards`, `n_txns`, `is_mega` (> 1,000 txns), `n_closed_cases`, `n_confirmed`, `n_cleared`.
- **DeviceProfile:** `profile_id` (hash), `device_info`, `os`, `browser`, `screen`, `quality` (1–4 fields known), `first_seen`, and time-bounded degree computed at query time.
- **BillingRegion:** `addr1`, `country` (`addr2`), `n_customers` (static, for hub rule).
- **EmailDomain:** `domain`, `n_customers`.
- **ClosedCase:** all 15 fields + `device_text` extracted from notes, `pattern`, `outcome`, `report_filed`, notes embedding.
- **Case:** all answer fields (`status`, `verdict`, `fraud_probability`, `pattern`, `affected_txn_ids`, `evidence`, `exposure_usd`, `as_of`), plus embedding of `summary`.

### Recommended fraud-investigation queries (all take `as_of = case.opened_at`)
1. `card_window(card_id, from, to)` — txns on card in window with ProductCD, amount, channel, region, device.
2. `card_baseline(card_id, as_of)` — history length, ProductCD shares, amount percentile/z, modal `addr1`, devices seen, days active.
3. `region_novelty(card_id, addr1, as_of)` — share of history in region, home region, foreign flag.
4. `device_neighbors(device_id, as_of, days=30, max_degree=100)` — other cards/customers, their closed cases and labelled fraud; excludes hubs.
5. `device_history(card_id, device_id)` — first seen on this card/customer, `id_15`, proxy.
6. `burst_detect(card_id, as_of, window=1h/48h)` — small-amount (<$5) count, larger follow-up, total, distinct devices/products.
7. `region_cluster(addr1, as_of, days=14)` — cards with confirmed fraud (skip if degree > 100).
8. `email_link(recipient_domain, as_of)` — only for rare domains (< 50 customers).
9. `prior_cases(customer_id/card_id, as_of)` — repeat victim, outcome, pattern.
10. `similar_cases(text/features, k)` — vector + structured retrieval over closed cases, filtered by `closed_at ≤ as_of` and same channel.
11. `ring_expand(device_id, as_of)` — 2-hop: device → cards → other devices/regions → closed cases; returns candidate connected cards for `MONITOR_CONNECTED_CARDS`.
12. `fingerprint_burst(as_of, amount 440–500, ProductCD C, window 1h)` — cross-customer burst scan (offline/monitoring mode).
13. `exposure(txn_ids)` — Σ|amount|.
14. `write_case(case)` / `find_cases(device|region|card)`.

### Recommended evidence hierarchy (strongest → weakest)
1. **Customer/step-up response** (simulated; must be recorded in `evidence_requests`).
2. **Confirmed-fraud closed case linked to the same low-degree entity** (device ≤ 20 customers, or same card/customer prior confirmed fraud).
3. **Cross-card shared low-degree device profile with fraud in the window** (as of `opened_at`), including the ring profile.
4. **Sequence/behavioural structure on the card**: burst, small→large, product/amount inconsistent with baseline, region ≠ home while home activity continues.
5. **Channel-specific identity anomalies**: `id_15 = New` + proxy + first-seen device (weak alone: 98% of cleared alerts are New).
6. **Model features** (C/D/M/V, `dist`), cited by name and flagged as unnamed.
7. **`risk_score`** (input, lowest; cleared alerts ≥ 0.81, seeded fraud ~0.14).
8. **Hub-type links** (null device, big regions, top emails): never evidence.

Retrieved closed cases are **memory for context and base rates**, not proof for a new case.

---

*Reproducibility:* analysis scripts were run in the session scratchpad (pandas/scipy) on the original CSVs; they are not part of the repository. Key seeds: baseline sample `random_state=0/1`.
