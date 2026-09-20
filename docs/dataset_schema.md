# Dataset schema (for graph design)

Companion to `../DATASET_SPEC.md`. Facts only — no TigerGraph schema is proposed here beyond listing candidates. "Src" = file/column. Counts measured programmatically.

## 1. Entities and identifiers

| Entity | Identifier | Source | Distinct | Format / notes |
|---|---|---|---|---|
| Customer | `customer_id` | transactions, closed cases, case pack | 13,553 | `C00001`…; 1:1 with `card1`. Skewed: median 4 txns, max 14,932 |
| Card | `card_id` | closed cases, case pack | ≥ 14,893 | `<customer_id>-K<n>` (K1 11 / K2 9 in case pack; K1/K2/K3 in closed cases). **Absent from transactions.csv** — candidate key `customer_id + card2..card6` (14,893 combos). K-index mapping unresolved |
| Transaction | `TransactionID` | transactions, identity, closed cases, case pack | 590,742 | int 3,000,001–3,590,742; unique; not time-ordered |
| Identity record | `TransactionID` | identity.csv | 144,432 | 1:1 with an online txn; 6,640 online txns lack one |
| Device profile | derived: `DeviceInfo` + `id_30` + `id_31` + `id_33` | identity.csv | 9,706 | Rendered `DeviceInfo \| OS \| browser \| screen`. 4,789 shared by >1 customer; all-null profile = hub of 1,011 customers |
| Email domain | `P_emaildomain`, `R_emaildomain` | transactions | 59 / 60 | Coarse; P missing 16%, R missing 76.7% (in-person 100%) |
| Billing region | `addr1` (+`addr2` country) | transactions | 332 / 74 | 43% null on online txns; 87 = home country |
| Product code | `ProductCD` | transactions | 5 | W (in-person) C R H S |
| Closed case | `case_id` `CC-nnnn` | closed_cases_history | 5,565 | 4,665 confirmed, 900 cleared |
| Exam case | `case_id` `HHG-nnn` | case_pack | 20 | |
| Merchant / terminal | — | — | — | **Not in data** |

## 2. transactions.csv — field roles (397 cols)

| Field(s) | Type | Role for graph / analysis | Leakage / caution |
|---|---|---|---|
| `TransactionID` | int | Vertex key | Not chronological |
| `TransactionDT` | int (s) | Time | = `ts` − 2016-07-02; use either |
| `ts` | datetime string | Vertex attr; NEXT ordering; windows | Case must only use `ts ≤ opened_at` |
| `TransactionAmt` | float USD | Attr; exposure sums; card-testing (<$5) | 0.27–31,937.38 |
| `ProductCD` | cat | Attr; product-fit vs history | W ⇔ in_person |
| `channel` | cat | Attr | `in_person` 439,670 / `online` 151,072 |
| `risk_score` | float 0–1 | Attr; alert trigger | Input, not answer; cleared cases 0.81–0.99, ring 0.14 |
| `customer_id` | str | Vertex key / edge | |
| `card1` | int | = customer (disguised) | Redundant |
| `card2,3,5` | float | Card attributes / card key | |
| `card4` | cat | Network (visa, mastercard, american express, discover) | |
| `card6` | cat | Type (debit, credit, debit or credit, charge card) | |
| `addr1` | float | Billing-region vertex | |
| `addr2` | float | Billing country attr | |
| `dist1`, `dist2` | float | Behavior signal | 60% / 94% null |
| `P_emaildomain` | str | Email-domain vertex | Weak hub |
| `R_emaildomain` | str | Recipient email (R6 "recipient email") | Online only |
| `C1–C14` | float | Unnamed count features | Use as signals; say so |
| `D1–D15` | float | Unnamed day-delta features | 0.2–93% null |
| `M1–M9` | T/F | Match flags (M4 has 3 values) | 29–59% null; ATO signal per README |
| `V1–V339` | float | Vesta engineered features | Block-wise missingness (App. B of spec) |

## 3. identity.csv — field roles (41 cols)

| Field(s) | Role | Note |
|---|---|---|
| `TransactionID` | Join key → transactions | 100% found |
| `DeviceInfo` | Device profile part 1 | 17.7% null (1,786 values) |
| `id_30` | OS | 46% null |
| `id_31` | Browser | 2.7% null (130 values) |
| `id_33` | Screen | 49% null |
| `id_15` | Device `New` / `Found` / `Unknown` | Key for pattern 3; 2.25% null |
| `id_23` | Proxy: TRANSPARENT / ANONYMOUS / HIDDEN | 96.3% null (= no proxy) |
| `id_34` | Match status `match_status:-1/0/1/2` | 46% null |
| `DeviceType` | mobile / desktop | |
| `id_28`, `id_29`, `id_16`, `id_12` | Found/New/NotFound flags | Undocumented in README |
| `id_35–id_38` | T/F flags | Undocumented |
| `id_01–id_11`, `id_13,14,17–22,24–26` | Unnamed numeric ratings | Use as signals |

## 4. closed_cases_history.csv (5,565 rows × 15)

| Field | Type | Null | Meaning / relation |
|---|---|---|---|
| `case_id` | str `CC-0001` | 0 | Key |
| `customer_id` | str | 0 | → Customer |
| `card_id` | str | 0 | → Card (1,913 distinct) |
| `opened_at` / `closed_at` | datetime | 0 | Jul 2–Nov 2 / Jul 4–Nov 6 |
| `outcome` | `confirmed_fraud` (4,665) / `cleared` (900) | 0 | **Label**. Prior fraud case = confirmed_fraud |
| `pattern` | 5 patterns + `undocumented` (9) + `none` (cleared) | 0 | Label |
| `first_fraud_txn_id` | float→ int | 16.17% | Null exactly for cleared; else = first of `txn_ids` |
| `txn_ids` | pipe-sep TransactionIDs | 0 | 14,955 unique txns, no overlap; for cleared = the one alerted txn |
| `n_txns` | int | 0 | 1–356 |
| `exposure_usd` | float | 0 | = Σ amounts of `txn_ids` for fraud; 0 for cleared |
| `connected_card_ids` | pipe-sep card_ids | 99.93% | Only 4 undocumented cases (23–24 cards) |
| `actions_taken` | pipe-sep | 0 | 3 combos only |
| `report_filed` | Yes/No | 0 | 397 Yes |
| `analyst_notes` | text | 0 | Templated per pattern; embedding source. Some cite device string |

## 5. case_pack.csv (20 rows × 8)

| Field | Type | Null | Meaning |
|---|---|---|---|
| `case_id` | `HHG-001…020` | 0 | Output filename |
| `opened_at` | datetime | 0 | 1–6 h after flagged txn `ts`; 2016-11-12 → 12-29 |
| `trigger_type` | risk_score (11) / customer_report (8) / analyst_request (1) | 0 | |
| `trigger_text` | text | 0 | Embeds amount, region/online, score, or customer message |
| `flagged_txn_id` | int | 0 | → Transaction (exists; not in any closed case) |
| `card_id` | str | 0 | → Card |
| `customer_id` | str | 0 | → Customer (matches txn) |
| `risk_score` | float | 9 | Only for risk_score triggers; equals txn score |

## 6. Relationships

| From | To | Cardinality | Key / derivation |
|---|---|---|---|
| Customer | Card | 1 : 1–4 | `card_id` prefix / distinct card2..6 combos (12,308 customers have 1) |
| Card | Transaction | 1 : N | customer + card2..6 (K mapping unresolved) |
| Transaction | Identity | 1 : 0..1 | `TransactionID` (online only, 95.6% coverage) |
| Transaction | Device profile | N : 1 | via identity fields |
| Transaction | Email domain (P and R) | N : 1 each | columns |
| Transaction | Billing region | N : 1 | `addr1` (+ addr2) |
| Transaction | Transaction (next) | 1 : 1 | order by `ts` within card |
| ClosedCase | Customer / Card | N : 1 | `customer_id`, `card_id` |
| ClosedCase | Transaction | 1 : N | `txn_ids` (fraud set 14,055; cleared 900) |
| ClosedCase | Card (connected) | N : N | `connected_card_ids` (4 cases) |
| ExamCase | Transaction / Card / Customer | 1 : 1 | `flagged_txn_id`, `card_id`, `customer_id` |
| Device profile | Customer/Card | N : N | shared device = cross-card link |
| Billing region | Card | N : N | shared region = cross-card link |

## 7. Recommended graph candidates (not yet a schema)

**Vertices:** Customer, Card, Transaction, DeviceProfile, EmailDomain, BillingRegion, ClosedCase; later Case (agent-written), PolicyDoc/Chunk (vector store).
**Edges:** OWNS, MADE, NEXT, FROM_DEVICE, PURCHASER_EMAIL, RECIPIENT_EMAIL, BILLED_IN, INVOLVES, ON_CARD, CONNECTED_TO, INVESTIGATES (Case→flagged Transaction).
**Store as transaction attributes (not vertices):** amount, ProductCD, channel, ts, risk_score, `id_15`, `id_23`, `DeviceType`, C/D/M subsets, selected V. Full 393 columns need not all be loaded.
**Pre-compute candidates:** card-level baselines (regions, products, devices, typical amounts) from history before `opened_at`; device-profile → card counts within time windows.

## 8. Known data-quality hazards for the schema

- `card_id` must be reconstructed (unresolved).
- Null/common device profiles are false hubs; email domains and `addr2=87` too.
- Mega-customers (up to 14,932 txns).
- No merchant entity.
- Labels only for Jul–Oct; cleared cases carry `exposure_usd = 0`.
- Data after a case's `opened_at` exists in the file; filter by `ts`.
