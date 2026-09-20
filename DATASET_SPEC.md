# DATASET_SPEC — TigerGraph × Hacker House Goa Fraud Investigation (IEEE-CIS edition)

Reconnaissance only. No graph, agent, or answers to benchmark cases are produced here. All numbers below were computed programmatically from the files in this folder (pandas); `transactions.csv` was never dumped. Where a statement comes from README.md rather than measurement it is marked *(README)*. Things measured that README does not say are marked **[measured]**. Open questions are collected in §21.

---

## 1. Dataset purpose

Six months (2016-07-02 → 2016-12-31) of card transactions from the public IEEE-CIS Fraud Detection data (Vesta), **all 393 original columns retained**, with the binary fraud label removed and replaced by a bank-model `risk_score`. Added on top: `customer_id`, real `ts`, `channel`, `risk_score`, a history of finished investigations (`closed_cases_history.csv`), and a 20-case exam (`case_pack.csv`). *(README)*

The task: load into TigerGraph, build an agent that investigates each case → decides fraud type, extent, and next best action under the Fraud Policy, knows when to ask for more evidence, and writes a case + optional SAR + next-best-action JSON per case. The only ground truth in the data is the closed cases (July–October). Exam cases are all November–December, i.e. **after** every closed case.

## 2. Files

| File | Size | Rows | Cols | Role |
|---|---|---|---|---|
| `README.md` | 38.7 KB | – | – | Task, glossary, patterns, regulators, policy (R1–R10), answer format, case table |
| `transactions.csv` | 707.9 MB | 590,742 | 397 | Fact table: 393 Vesta columns + 4 added |
| `identity.csv` | 26.7 MB | 144,432 | 41 | Device/connection record for online transactions (join on `TransactionID`) |
| `closed_cases_history.csv` | 2.7 MB | 5,565 | 15 | Labeled history / case memory (Jul–Oct) |
| `case_pack.csv` | 3.5 KB | 20 | 8 | The 20 exam alerts (Nov–Dec) |

UTF-8, header row, USD amounts.

## 3–6. Row counts, columns, types, missingness

Row counts are in §2. Full per-column tables (dtype, missing %, unique count) are in **Appendix A (transactions), B (V-blocks), C (identity)**; closed-case and case-pack columns are in §12–13.

Type summary for `transactions.csv` **[measured]**: 377 float64, 17 object, 3 int64.
Object columns: `ProductCD, card4, card6, P_emaildomain, R_emaildomain, M1–M9, customer_id, ts, channel`. Ints: `TransactionID, TransactionDT, card1`.
Notable missingness **[measured]**: `addr1/addr2` 11.13% (online 43.4%, in-person 0.1%); `dist1` 59.7%; `dist2` 93.6%; `P_emaildomain` 16.0%; `R_emaildomain` 76.7% (100% missing in-person); `D` columns 0.2%–93.4%; `M1–M9` 28.7%–59.4%; V columns are missing in blocks (see Appendix B). Core keys (`TransactionID, TransactionDT, TransactionAmt, ProductCD, customer_id, ts, channel, risk_score, C1–C14`) have 0% missing.

## 7. Cardinalities of important identifiers **[measured]**

| Identifier | Distinct | Notes |
|---|---|---|
| `TransactionID` | 590,742 | Unique; contiguous 3,000,001 → 3,590,742; monotone in file order but **not** in time order (IDs were reassigned) |
| `customer_id` | 13,553 | 1:1 with `card1` (13,553 distinct card1). Txns/customer: median 4, mean 43.6, max 14,932 (heavy tail — some "customers" are merged high-volume issuers) |
| `card_id` (`Cxxxxx-Kn`) | ≥ 14,893 card combos | **Not a column in transactions.csv** — see §11 and §21 |
| distinct (card2,card3,card4,card5,card6) | 14,893 | 12,308 customers have 1, 1,157 have 2, 81 have 3, 7 have 4 |
| `addr1` (billing region) | 332 | Median 2 customers/region, max 2,006 |
| `addr2` (country) | 74 | 87 = home country (88.1% of rows) |
| `P_emaildomain` | 59 | gmail.com 228k, yahoo 101k, hotmail 45k, anonymous.com 37k |
| `R_emaildomain` | 60 | 76.7% missing |
| `DeviceInfo` (identity) | 1,786 | |
| Device profile (DeviceInfo+id_30+id_31+id_33) | 9,706 | 4,789 shared by >1 customer; largest = all-null profile (1,011 customers) |
| `ProductCD` | 5 | W 439,670 · C 68,721 · R 37,699 · H 33,024 · S 11,628 |
| Closed cases | 5,565 | 1,892 customers, 1,913 card_ids |

## 8. Date/time ranges **[measured]**

| Field | Range |
|---|---|
| `transactions.ts` | 2016-07-02 00:02:21 → 2016-12-31 23:58:54 |
| `TransactionDT` | 141 → 15,811,134 seconds; `ts = 2016-07-02 00:00:00 + TransactionDT` exactly (0 mismatches) |
| Monthly txns | Jul 130,295 · Aug 93,786 · Sep 92,903 · Oct 100,420 · Nov 84,805 · Dec 88,533 |
| Closed-case txns | 2016-07-02 → 2016-10-31 |
| Closed `opened_at` | 2016-07-02 07:17 → 2016-11-02 02:00 |
| Closed `closed_at` | 2016-07-04 02:10 → 2016-11-06 23:39 (open→close 16–142 h) |
| Case pack `opened_at` | 2016-11-12 → 2016-12-29 |

`opened_at` in the case pack is 1–6 h **after** the flagged txn `ts` (never equal). In closed cases `opened_at` is 1 h–~65 days after the first fraud txn (median 18 h).

## 9. Meaning of important columns

**Added by TigerGraph** *(README)*: `customer_id` (`C01234`, derived from card issuer field `card1`; a customer may hold several cards), `ts`, `channel` (`in_person` ⇔ `ProductCD = W` and no identity record — verified 100%; `online` = all others), `risk_score` (0–1, two decimals, 99 distinct values, mean 0.171; 16,871 txns > 0.70).

**Vesta originals** *(README; names are unpublished)*: `TransactionDT` (seconds since start; disguised), `TransactionAmt` (USD; disguised; 0.27–31,937.38), `ProductCD`, `card1–6` (`card4` network, `card6` type; rest issuer codes), `addr1` (billing region), `addr2` (billing country), `dist1/2`, `P_/R_emaildomain` (purchaser / recipient), `C1–C14` (counts, e.g. addresses/phones associated with card), `D1–D15` (day deltas, e.g. since previous txn), `M1–M9` (match flags, values T/F; M4 has 3 values), `V1–V339` (engineered features). Identity: `id_01–id_11` numeric ratings; `id_12–id_38` categorical; readable ones: `id_15` (device New/Found/Unknown), `id_23` (proxy: TRANSPARENT/ANONYMOUS/HIDDEN), `id_30` (OS), `id_31` (browser), `id_33` (screen), `id_34` (match status), `DeviceType`, `DeviceInfo`. `id_28`/`id_29` (New/Found; Found/NotFound) are similar in spirit to `id_15`/`id_16` **[measured, not documented]**. The README instructs to use unnamed columns as signals and be honest about not knowing what they mean.

## 10. Relationships between files

```
customer_id (13,553) ──1:1── card1
customer_id ──1..4── card (card_id = customer + Kn)
Card ──1:N── Transaction (transactions.csv)
Transaction ──1:0..1── Identity row (identity.csv, key TransactionID; online only)
ClosedCase ──N:1── Customer / Card;  ClosedCase ──1:N── Transaction (txn_ids)
ClosedCase ──N:N── Card (connected_card_ids; only 4 cases populated)
CasePack row ──1:1── flagged Transaction, Card, Customer
```
Integrity **[measured]**: every `identity.TransactionID` exists in transactions (100%); 144,432 of 151,072 online txns (95.6%) have an identity row — **6,640 online txns have no identity record**; no in-person txn has one. Every closed-case `txn_id` (14,955, all unique, no txn in two cases) exists in transactions and belongs to the case's customer. All 20 flagged txns exist and match the case-pack customer.

## 11. How transactions connect to entities

| Entity | How derived | Caveat |
|---|---|---|
| Customer | `transactions.customer_id` | Given |
| Card | **No `card_id` column.** Case/closed-case card IDs are `customer_id + "-K" + n`. Candidate card key: (`customer_id`, `card2..card6`). | Mapping to `Kn` is **not** derivable by simple ordering (first-seen / frequency / sorted tested; ≤9% match on multi-card customers). Single-combo customers are always `K1`; `K2/K3` occur only for customers with ≥2 combos. In closed cases, 41 card_ids span 2 combos and one spans 3, i.e. combo≠card in a few cases. Must be resolved (§21) |
| Device profile | Join identity → `DeviceInfo + id_30 + id_31 + id_33` (README definition) | All-null profile is a "shared" hub of 1,011 customers — exclude/handle. Identity `DeviceInfo` missing for 17.7% |
| Email domain | `P_emaildomain`, `R_emaildomain` | Very coarse (59 domains, each shared by hundreds–thousands of customers) — a weak link except rare domains |
| Billing region | `addr1` (+`addr2`) | 332 regions; null for 43% of online txns |
| Merchant | **Not present.** No merchant/terminal ID exists. Closest proxies: `ProductCD`, `R_emaildomain`, amount, `dist1/2` | README's "merchant" language (R7 recurring merchant) cannot be evaluated directly |
| Account | Not separate from customer/card | |
| Time order | `ts` (sort by `TransactionDT`); `TransactionID` order ≠ time order | Build `NEXT` edges by `ts` per card |

## 12. How closed cases are represented

Columns *(README)*: `case_id, customer_id, card_id, opened_at, closed_at, outcome, pattern, first_fraud_txn_id, txn_ids (pipe-sep), n_txns, exposure_usd, connected_card_ids (pipe-sep), actions_taken (pipe-sep), report_filed (Yes/No), analyst_notes`. Types/missing **[measured]**: `first_fraud_txn_id` float64, 16.17% null (exactly the 900 cleared cases); `connected_card_ids` 99.93% null (4 populated); all others 0% null. IDs `CC-0001…CC-5565`.

**What is a "prior fraud case"?** `outcome == confirmed_fraud` (4,665). Cleared = `outcome == cleared` (900), `pattern == none`.

| Pattern | Confirmed | Report filed Yes |
|---|---|---|
| card_not_present_fraud | 1,404 | 14 |
| account_takeover | 1,205 | 187 |
| card_not_present_new_device | 1,076 | 56 |
| out_of_region_use | 955 | 124 |
| card_testing | 16 | 7 |
| undocumented | 9 | 9 |
| none (cleared) | 0 (900 cleared) | 0 |

Total 397 reports. `actions_taken` takes only 3 values: `CREATE_CASE|BLOCK_CARD` (4,268), `CREATE_CASE|BLOCK_CARD|FILE_REPORT` (397), `VERIFY_WITH_CUSTOMER|CLOSE_NO_FRAUD` (900).

Structure **[measured]**: `n_txns` mean 2.7, median 1, max 356; `exposure_usd` mean $372, max $35,032. `first_fraud_txn_id` = first id of `txn_ids` for all 4,665 fraud cases. Exposure equals the sum of `TransactionAmt` of `txn_ids` for all 4,665 confirmed cases; **cleared cases have exposure 0 although `txn_ids` holds the single alerted txn**. Each case has one customer, one card_id. Cleared cases: `n_txns = 1` and analyst notes are one of three templates (716 "travel to billing region", 158 "new phone, device added to profile", 26 "unusual amount but consistent with intent").

**Fraud-txn set** = `txn_ids` of confirmed cases = 14,055 unique txns (of 590,742; 2.4%) — the only labeled fraud. Their channel: 7,946 online, 6,109 in-person. Only txns from Jul–Oct have labels; **Nov–Dec is entirely unlabeled**.

**Case memory** usable: `analyst_notes` (text, templated by pattern; embed for vector search), `pattern`, `outcome`, `actions_taken`, `report_filed`, `txn_ids` → device profile/region/email/amount fingerprints, `connected_card_ids` (ring), customer_id/card_id (repeat-victim lookup). 21 customers have >1 card_id among cases.

**The undocumented cases (9: CC-2649, 2971, 2985, 3035, 3748, 3841, 3907, 4086, 4124)** **[measured]**: two groups. *Group 1* (CC-2649, 2971, 2985, 3035; opened Aug 27–Sep 4; 2–3 txns, $108–$390 each case): all txns come from device profile `SM-G935F Build/NRD90M | Android 7.0 | chrome 62.0 for android | 1920x1080`, anonymous proxy, `id_15 = New`, mobile, `ProductCD = C`, amounts ≤ $500; the notes say "two other cardholders reported the same device profile this month" and each carries a `connected_card_ids` list of 23–24 cards (the only 4 populated values in the file). *Group 2* (CC-3748, 3841, 3907, 4086, 4124; opened Sep 20–29): 4 txns per case, ~$1.87k–$1.92k per case, `ProductCD = C`, no connected cards listed, and these txns have no identity record. All 9 filed SARs. Mean risk score of these 30 txns is **0.14** (fraud that hides under the model). The profile SM-G935F/chrome/anonymous-proxy occurs for 52 customers overall in identity.csv, so the shared-device footprint extends beyond the labeled cases.

## 13. How case_pack.csv represents benchmark cases

Columns: `case_id (HHG-001…HHG-020), opened_at, trigger_type, trigger_text, flagged_txn_id, card_id, customer_id, risk_score`. 20 rows, 20 unique ids. `risk_score` null for the 9 non-risk-score triggers.

| Trigger | Count | Cases |
|---|---|---|
| risk_score | 11 | 001, 002, 005, 007, 010, 012, 013, 015, 017, 019, 020 |
| customer_report ("I never made this $X purchase") | 8 | 003, 004, 006, 008, 009, 011, 016, 018 |
| analyst_request (shared unusual device, look for related activity) | 1 | 014 |

Card suffix: 11 × K1, 9 × K2. Channel of flagged txn: 15 online, 5 in-person (001, 003, 007, 012, 018). The case pack risk_score equals the transaction's `risk_score` in transactions.csv. Note customer-report txn scores are stored in transactions (0.25–0.48 for these) but hidden in the trigger — see §14. 16 of 20 customers (and cards) have prior closed cases; none of the 20 flagged txns appears in a closed case. Customer history size ranges from 36 to 10,361 txns. Cases are not solved or classified here.

## 14. Information available BEFORE additional evidence is requested

- Case-pack row: trigger type/text, flagged txn id, card, customer, opened_at, alert risk score (risk-score triggers only).
- Full graph: every txn of every card/customer, before and after the flagged txn (the dataset contains the future of Nov–Dec too — an agent should restrict itself to `ts ≤ opened_at` to behave realistically; see §20).
- Per-txn `risk_score` for every txn (including customer-report cases where the trigger omits it).
- Identity/device record (online), billing region, email domains, C/D/M/V features.
- Closed-case memory (Jul–Oct) and the README pattern/policy/regulatory text.
- Cross-card links (shared device profile, region, recipient email).

## 15. Information that can be additional evidence

The dataset has **no** customer or analyst replies *(README)*. Additional evidence is therefore **simulated**: `customer_validation` (customer confirms/denies), `step_up_auth` outcome, `analyst_info`. Each must be recorded in `evidence_requests` with `assumed_response`, and `next_best_actions.final` must reflect it. Data-side "extra evidence" that costs graph calls rather than a request: other cards on the same device/region/email, the card's history, closed-case retrieval.

## 16. The five documented fraud patterns *(README)*

1. **Card testing** – ≥3 tiny (<$5) online authorizations, then a larger purchase. Confirmed by sequence. Policy R5.
2. **Card-not-present fraud** – online use with amounts/products out of history, often 2–4 txns in 48 h. One unusual purchase alone is ambiguous → verify. R1–R4.
3. **CNP from a new device** – as 2, identity `id_15 = New` for the account, sometimes behind a proxy. Stronger, still not proof.
4. **Out-of-region use** – card-present in a billing region with no history while home activity continues. Several days in one new region = trip, not a clone. R2, R3.
5. **Account takeover** – mixed-channel activity inconsistent with the cardholder, device and match-flag anomalies; stolen credentials.

Plus `undocumented` (see the ring in §12) and `none`. README states the five are **not exhaustive** and finding an undocumented pattern is scored. Measured labeled channel mix: CNP & new-device & testing are online-only; out-of-region is in-person-only; ATO is mixed (3,641 in-person, 971 online).

## 17. Fraud policy *(README, v1.0)*

**Actions (14):** `ALLOW_TRANSACTION, DECLINE_TRANSACTION, MONITOR_CARD, MONITOR_CONNECTED_CARDS, WARN_CUSTOMER, VERIFY_WITH_CUSTOMER, STEP_UP_AUTH, BLOCK_CARD, BLOCK_ALL_CARDS, GENERATE_REPORT, CREATE_CASE, FILE_REPORT, ESCALATE_TO_ANALYST, CLOSE_NO_FRAUD`. Exact identifiers required.

**Routes:** `auto` = ALLOW, MONITOR_CARD, MONITOR_CONNECTED_CARDS, WARN_CUSTOMER, VERIFY_WITH_CUSTOMER, STEP_UP_AUTH, GENERATE_REPORT, CREATE_CASE, ESCALATE_TO_ANALYST, CLOSE_NO_FRAUD. `L1` = DECLINE_TRANSACTION; BLOCK_CARD when exposure ≤ $2,500. `L2` = BLOCK_CARD when exposure > $2,500; BLOCK_ALL_CARDS always; FILE_REPORT always. Only `auto` actions may be executed; L1/L2 are recommended and wait.

**Rules:**
- **R1** single weak signal (incl. score alone) with P<0.70 → VERIFY_WITH_CUSTOMER or STEP_UP_AUTH before any block.
- **R2** customer denies → BLOCK_CARD + CREATE_CASE; add FILE_REPORT if exposure > $1,000 or shared device profile / another card's fraud.
- **R3** customer confirms → CLOSE_NO_FRAUD, note confirmation.
- **R4** no reply in 24 h → MONITOR_CARD + DECLINE_TRANSACTION for pending; escalate if exposure > $500.
- **R5** card testing (≥3 small online auths on one card within an hour, then larger purchase) → DECLINE_TRANSACTION + STEP_UP_AUTH; if purchase > $100 already cleared → BLOCK_CARD.
- **R6** several cards fraud from same device profile / billing region / recipient email in one window → name the shared element; CREATE_CASE, FILE_REPORT, MONITOR_CONNECTED_CARDS for every sharing card.
- **R7** customer disputes a charge matching their own recurring pattern (same merchant, amount, monthly) → CREATE_CASE + VERIFY_WITH_CUSTOMER + WARN_CUSTOMER; do not block.
- **R8** verdict uncertain and exposure > $500, or evidence conflicts → ESCALATE_TO_ANALYST.
- **R9** undocumented coordinated/repeated abuse across customers → CREATE_CASE, FILE_REPORT, ESCALATE_TO_ANALYST; describe pattern in own words.
- **R10** never BLOCK_ALL_CARDS unless ≥2 of the customer's cards show confirmed fraud or credentials confirmed compromised.

**Case vs report (§3a):** open a case at P ≥ 0.30, on any evidence request, or any customer dispute. File a SAR only if fraud confirmed/strongly suspected **and** (exposure > $1,000 **or** shared device/region cluster/another customer's fraud **or** coordinated/undocumented). A SAR always has a case behind it.
**Exposure (§4):** sum of |amount| of all identified episode txns incl. the flagged one.
**Evidence (§5):** may request customer validation, step-up, analyst info without approval; responses simulated.
**Stopping (§6):** P ≥ 0.85 or ≤ 0.15 with ≥2 independent evidence pieces; or a verification settles it; or further steps unlikely to change decision (state in `stop_reason`). Both over- and under-investigation are penalised.
**Explaining (§7):** cite evidence used, why more was requested, and rule numbers.

Note (measured): the closed-case actions use only 3 action combos; they do not model P/step-up/verification flows.

## 18. Regulatory references *(README)*

Links only — none of the documents are in the folder; they must be fetched separately if used for vector retrieval.
- **FinCEN (8):** SAR Filing FAQs Oct 2025; SAR Narrative Guidance; Preparing a Complete and Sufficient SAR Narrative; SAR Supporting Documentation FIN-2007-G003; SAR Activity Review TTI 19; Advisory on Account Takeover FIN-2011-A016; Advisory on Imposter Scams & Money Mule Schemes; Identity-Related Suspicious Activity 2021.
- **FATF (6):** Illicit Financial Flows from Cyber-Enabled Fraud; New Payment Methods; Professional Money Laundering; Remittance & Currency Exchange Providers; Trade-Based ML; International Co-operation on ML.
- **FFIEC (2):** ML/TF Red Flags (Appendix 07); Suspicious Activity Reporting.
- **OFAC (1):** SDN list.

## 19. Required answer/output format *(README)*

One JSON per case, `cases/<case_id>.json`, 20 files. Top level: `case_id, case, evidence_requests, next_best_actions, sar, stop_reason, tool_calls, tokens, latency_s`.
- `case`: `status` (open|closed_fraud|closed_legitimate|escalated), `verdict` (fraud|legitimate|uncertain), `fraud_probability` (0–1, calibrated), `pattern` (card_testing|card_not_present_fraud|card_not_present_new_device|out_of_region_use|account_takeover|undocumented|none), `pattern_description` (required iff undocumented else ""), `affected_txn_ids`, `first_suspicious_txn_id`, `connected_card_ids`, `connected_device_profiles`, `exposure_usd`, `evidence[{claim, source(graph|document|customer|external), ref, entity_ids}]`, `similar_prior_cases` (CC-ids), `summary` (2–6 sentences), `written_to_graph`, `graph_case_id`.
- `evidence_requests[{type(customer_validation|step_up_auth|analyst_info), asked_after_step, assumed_response}]`.
- `next_best_actions`: `initial[]`, `final[]` (each `{action, route, reason(cites rule)}`), `what_changed` ("nothing" if same).
- `sar`: `file, reason, narrative (6–12 sentences, self-contained; ""), subjects, total_amount_usd, activity_dates[first,last] (YYYY-MM-DD)`. If `file` false: narrative "", subjects [], total 0, dates []. `sar.file` must equal presence of FILE_REPORT in final actions.
- Legitimate verdict → `affected_txn_ids` empty, `exposure_usd` 0, `sar.file` false.
- Device profile string format: `DeviceInfo | OS | browser | screen`.
- Optional: separate folder for autonomous monitoring/extra investigation (Innovation, not accuracy).

## 20. Constraints and pitfalls (README + measured)

README: risk score is never a verdict; half the cases are legitimate — blocking everything scores badly; five patterns are not exhaustive; devices and regions connect people, some cases need other cards' activity; unnamed features may be used but must be described honestly; customer/analyst replies are simulated and assumption recorded; every ID in answers must exist in the dataset (made-up IDs score zero); missing fields score zero; do not use the public IEEE-CIS/Kaggle files (disqualification); IDs/times/amounts were disguised; "a small number of rows were added to seed investigation exercises" — i.e. some rows are synthetic and their features may look off.

Measured pitfalls:
- **Cleared cases all had high scores** (0.81–0.99, mean 0.88) while confirmed fraud averaged 0.47 and the ring only 0.14 — the score is anti-informative at the top end in this history.
- **Future leakage:** the file contains activity after each case's `opened_at`; restrict to `ts ≤ opened_at` for realism.
- **TransactionID is not chronological**; sort by `ts`.
- **Cleared closed cases have `exposure_usd = 0` yet list a txn** — do not count them as fraud txns.
- **`card_id` is not in transactions.csv** — must be reconstructed.
- **Mega-customers** (`customer_id` with up to 14,932 txns) behave like merged issuers, not single people; behavioral baselines need care.
- **All-null device profile** and very common profiles (Windows/chrome 63/1920x1080: 842 customers) are false hubs.
- Some hub values are meaningless links: email domains, `addr2=87`, `ProductCD`.
- 6,640 online txns lack identity; treat as unknown, not "no device".
- 157 same-customer, same-second duplicate-time txns exist.
- Closed cases exist only Jul–Oct; Nov–Dec (the exam window) has no labels, so novel/ring activity that began in Nov cannot be looked up by outcome.

---

## 21. Open questions / unknowns

1. How does `Cxxxxx-Kn` map to txns (which combo is K1/K2/K3)? Does the case card_id always match the flagged txn's card fields?
2. What is the "device profile" ID exactly when DeviceInfo/id_30/id_31/id_33 partially null?
3. Does the ring device profile (SM-G935F / chrome / anon proxy) appear in Nov–Dec activity, and on which cards (HHG-014 hints so)?
4. Which C/D/M/V columns separate the labeled fraud types from normal history (feature discovery vs. the labeled Jul–Oct set)?
5. How do the 20 cases look relative to history (out of region? new device? bursts?) — deliberately not analysed here.
6. Whether seeded ("added") rows are identifiable, and what they look like.
7. How `customer_id`/`card1` merged mega-customers should be handled.
8. Do "customer-report" txns' scores/behavior look different from risk-score-triggered ones?

---

## Appendix A — transactions.csv columns (excluding V1–V339)

Generated from the full file.

| Column | dtype | Missing | Unique |
|---|---|---|---|
| `TransactionID` | int64 | 0.00% | 590,742 |
| `TransactionDT` | int64 | 0.00% | 574,993 |
| `TransactionAmt` | float64 | 0.00% | 34,885 |
| `ProductCD` | object | 0.00% | 5 |
| `card1` | int64 | 0.00% | 13,553 |
| `card2` | float64 | 1.51% | 500 |
| `card3` | float64 | 0.26% | 114 |
| `card4` | object | 0.27% | 4 |
| `card5` | float64 | 0.72% | 119 |
| `card6` | object | 0.27% | 4 |
| `addr1` | float64 | 11.13% | 332 |
| `addr2` | float64 | 11.13% | 74 |
| `dist1` | float64 | 59.67% | 2,651 |
| `dist2` | float64 | 93.63% | 1,751 |
| `P_emaildomain` | object | 15.99% | 59 |
| `R_emaildomain` | object | 76.73% | 60 |
| `C1` | float64 | 0.00% | 1,657 |
| `C2` | float64 | 0.00% | 1,216 |
| `C3` | float64 | 0.00% | 27 |
| `C4` | float64 | 0.00% | 1,260 |
| `C5` | float64 | 0.00% | 319 |
| `C6` | float64 | 0.00% | 1,328 |
| `C7` | float64 | 0.00% | 1,103 |
| `C8` | float64 | 0.00% | 1,253 |
| `C9` | float64 | 0.00% | 205 |
| `C10` | float64 | 0.00% | 1,231 |
| `C11` | float64 | 0.00% | 1,476 |
| `C12` | float64 | 0.00% | 1,199 |
| `C13` | float64 | 0.00% | 1,597 |
| `C14` | float64 | 0.00% | 1,108 |
| `D1` | float64 | 0.21% | 641 |
| `D2` | float64 | 47.56% | 641 |
| `D3` | float64 | 44.53% | 649 |
| `D4` | float64 | 28.62% | 808 |
| `D5` | float64 | 52.48% | 688 |
| `D6` | float64 | 87.60% | 829 |
| `D7` | float64 | 93.41% | 597 |
| `D8` | float64 | 87.30% | 12,353 |
| `D9` | float64 | 87.30% | 24 |
| `D10` | float64 | 12.90% | 818 |
| `D11` | float64 | 47.31% | 676 |
| `D12` | float64 | 89.04% | 635 |
| `D13` | float64 | 89.51% | 577 |
| `D14` | float64 | 89.47% | 802 |
| `D15` | float64 | 15.11% | 859 |
| `M1` | object | 45.93% | 2 |
| `M2` | object | 45.93% | 2 |
| `M3` | object | 45.93% | 2 |
| `M4` | object | 47.67% | 3 |
| `M5` | object | 59.36% | 2 |
| `M6` | object | 28.70% | 2 |
| `M7` | object | 58.65% | 2 |
| `M8` | object | 58.65% | 2 |
| `M9` | object | 58.65% | 2 |
| `customer_id` | object | 0.00% | 13,553 |
| `ts` | object | 0.00% | 574,993 |
| `channel` | object | 0.00% | 2 |
| `risk_score` | float64 | 0.00% | 99 |


## Appendix B — V1–V339 blocks

| Column range | Missing % (min–max) | Unique (min–max) |
|---|---|---|
| V1–V11 | 47.3–47.3 | 2–10 |
| V12–V34 | 12.9–12.9 | 2–16 |
| V35–V52 | 28.6–28.6 | 2–55 |
| V53–V74 | 13.1–13.1 | 2–52 |
| V75–V94 | 15.1–15.1 | 2–32 |
| V95–V137 | 0.1–0.1 | 2–24414 |
| V138–V166 | 86.1–86.1 | 6–9621 |
| V167–V216 | 76.3–76.3 | 8–14951 |
| V217–V278 | 76.0–77.9 | 5–13358 |
| V279–V321 | 0.0–0.2 | 2–37367 |
| V322–V339 | 86.0–86.0 | 13–2453 |


## Appendix C — identity.csv columns

| Column | dtype | Missing | Unique |
|---|---|---|---|
| `TransactionID` | int64 | 0.00% | 144,432 |
| `id_01` | float64 | 0.00% | 77 |
| `id_02` | float64 | 2.34% | 115,655 |
| `id_03` | float64 | 54.02% | 24 |
| `id_04` | float64 | 54.02% | 15 |
| `id_05` | float64 | 5.12% | 93 |
| `id_06` | float64 | 5.12% | 101 |
| `id_07` | float64 | 96.43% | 84 |
| `id_08` | float64 | 96.43% | 94 |
| `id_09` | float64 | 48.06% | 46 |
| `id_10` | float64 | 48.06% | 62 |
| `id_11` | float64 | 2.27% | 365 |
| `id_12` | object | 0.00% | 2 |
| `id_13` | float64 | 11.75% | 54 |
| `id_14` | float64 | 44.48% | 25 |
| `id_15` | object | 2.25% | 3 |
| `id_16` | object | 10.33% | 2 |
| `id_17` | float64 | 3.38% | 104 |
| `id_18` | float64 | 68.72% | 18 |
| `id_19` | float64 | 3.42% | 522 |
| `id_20` | float64 | 3.45% | 394 |
| `id_21` | float64 | 96.42% | 490 |
| `id_22` | float64 | 96.42% | 25 |
| `id_23` | object | 96.34% | 3 |
| `id_24` | float64 | 96.71% | 12 |
| `id_25` | float64 | 96.44% | 341 |
| `id_26` | float64 | 96.42% | 95 |
| `id_27` | object | 96.42% | 2 |
| `id_28` | object | 2.27% | 2 |
| `id_29` | object | 2.27% | 2 |
| `id_30` | object | 46.18% | 75 |
| `id_31` | object | 2.74% | 130 |
| `id_32` | float64 | 46.18% | 4 |
| `id_33` | object | 49.14% | 260 |
| `id_34` | object | 46.03% | 4 |
| `id_35` | object | 2.26% | 2 |
| `id_36` | object | 2.26% | 2 |
| `id_37` | object | 2.26% | 2 |
| `id_38` | object | 2.26% | 2 |
| `DeviceType` | object | 2.37% | 2 |
| `DeviceInfo` | object | 17.71% | 1,786 |

