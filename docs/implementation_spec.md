# Implementation specification (Phase 5 — spec only)

Status: **specification; nothing here is installed, loaded, or built.** It fixes what Phase 6+ must build so that the build is mechanical and testable. It supersedes `docs/graph_architecture.md` wherever they differ (differences are listed in §16); numbers come from `docs/signal_validation.md` and are reproduced by `scripts/analysis/` (`python scripts/analysis/run_all.py`, 7/7 scripts, 56 checks passing).

Normative language: **MUST / MUST NOT / SHOULD**. No `[verify]` items remain in §2 (all resolved or explicitly recorded as unresolved in §2.0 E3/E6/E9 and §17).

**Environment (verified live 2026-09-19, `docs/tigergraph_environment.md` §7):** TigerGraph **4.2.5 Enterprise** on Savanna; existing graph `Transaction_Fraud` (read-only for us); our graph `FraudInvestigation` (not yet created). Constraints from that probe **and from the Phase 8 step-0 compatibility probe** (throwaway graphs `hhg_p8_tmp`/`hhg_p8_tmp2`, both dropped; `Transaction_Fraud` and the global schema verified byte-identical) are collected in §2.0 and frozen in §2.5. **Naming: the GSQL vertex type for the agent's investigation record is `FI_Case`** (`Case` is a reserved word); prose below still says "Case" for the concept.

---

## 1. Fixed constants and conventions

| Name | Value |
|---|---|
| `T0` | 2016-07-02 00:00:00 |
| `epoch` | integer seconds since `T0` (= `TransactionDT`; `ts = T0 + epoch`, verified 0 mismatches) |
| `as_of` | `case.opened_at` as epoch. The only time parameter of any investigation |
| `HIST_END` | 2016-11-01 (epoch 10,540,800 — `common.HIST_END_EPOCH`). End of the labelled history and of every closed case (latest `closed_at` = 2016-11-06 23:39:58; earliest benchmark `opened_at` = 2016-11-12 00:46:24) |
| Card ID | `customer_id + "-K" + n`, n = rank of the transaction's `card6` among that customer's distinct `card6` values, ascending, missing first. Reproduces 14,955/14,955 closed-case txns, 5,565/5,565 cases, 20/20 case-pack cards |
| Device profile string | `DeviceInfo \| id_30 \| id_31 \| id_33`, each null → `?`; identity rows only. `device_id = "D_" + md5(profile_str)[:10]` |
| Channel | `online` iff a device record exists or ProductCD ≠ W; `in_person` iff ProductCD = W (100% consistent) |
| Money | USD, `DOUBLE`; exposure = Σ\|amount\| |
| Case-pack IDs | `HHG-001`…`HHG-020`; closed-case IDs `CC-0001`…`CC-5565` |
| GSQL names | vertex `FI_Case` (not `Case`); attribute `proxy_type` (not `proxy`); every vertex declared `WITH primary_id_as_attribute="true"` (§2.0 E12–E14) |

---

## 2. Graph schema to implement

Graph name: `FraudGraph`. Types: TigerGraph `STRING, INT (64-bit), UINT, DOUBLE, FLOAT, BOOL, DATETIME`. "Evidence" column: **Y** = may be cited in a case file, **C** = returned as context only (never a filter/ranking key for a decision), **G** = gate/control field, never cited, **A** = audit only.

### 2.0 TigerGraph 4.2.5 constraints (all VERIFIED LIVE unless marked)

Sources: probe 1 (`scripts/tigergraph/probe_env.py`), Phase 8 step 0 (`probe_phase8_step0.py`: 65/67 checks OK, 2 inconclusive), reserved-word scan (`probe_reserved_words.py`), follow-up (`probe_phase8_step0b.py`, aborted at query install). Details and raw results: `docs/tigergraph_environment.md` §7.

| # | Constraint | Consequence for this spec |
|---|---|---|
| E1 | Graph is created with a **local schema**: `CREATE GRAPH FraudInvestigation ()`, then `CREATE SCHEMA_CHANGE JOB … FOR GRAPH FraudInvestigation { ADD VERTEX …; ADD DIRECTED EDGE …; }` and `RUN SCHEMA_CHANGE JOB …`. The global schema and `Transaction_Fraud` stayed byte-identical through every test | **Never** `CREATE GRAPH … (*)`, global `CREATE VERTEX/EDGE`, or `USE GLOBAL` writes |
| E2 | **Multi-pair edges with reverse edges are confirmed:** `ADD DIRECTED EDGE SIMILAR_CASE (FROM FI_Case, TO ClosedCase \| FROM FI_Case, TO FI_Case, score DOUBLE, …) WITH REVERSE_EDGE="SIMILAR_FROM"`; same for `DESCRIBES`. `SHOW EDGE` returns `…(FROM A, TO B\|FROM A, TO C, …) WITH REVERSE_EDGE=…` | Use as written |
| E3 | **Secondary indexes:** `ALTER VERTEX V ADD INDEX idx ON (attr);` in a local schema-change job was accepted for INT (`Transaction.epoch`, `ClosedCase.close_epoch`, `FI_Case.as_of_epoch`, `TextChunk.valid_from_epoch`) and STRING (`DeviceProfile.profile_str`) vertex attributes. **Existence is confirmed:** index names appear in `LS` and in `GET /gsql/v1/schema/graphs/<graph>`; they are **not** listed by `SHOW VERTEX`. **Speed benefit is UNRESOLVED:** at 360,000 vertices indexed `epoch == v` (827 ms), non-indexed `amount == v` (837 ms) and the indexed range (840 ms) were indistinguishable because REST latency (~0.8 s) dominated; the server-side timing follow-up was aborted | Create the indexes (cheap, harmless) but **do not rely on them for latency claims**; edge attributes cannot be indexed; measure server-side (`timestamp()` inside a query) in the real graph before tuning. Schema jobs run sequentially with timeouts ≥ 240 s (one INT-index job once exceeded 600 s in probe 1, then finished in later runs) |
| E4 | Vector: `ALTER VERTEX TextChunk ADD VECTOR ATTRIBUTE embedding(DIMENSION=<d>, METRIC="COSINE");`; load `LOAD f TO VECTOR ATTRIBUTE embedding ON VERTEX TextChunk VALUES ($0, SPLIT($1, ";")) USING SEPARATOR=",", HEADER="true", EOL="\n";` | `embedding` is added after the base job (not a `LIST<FLOAT>`); Enterprise edition (dimension ≤ 32,768); `<d>` fixed by the embedding model (blocker B5) |
| E5 | `vectorSearch({TextChunk.embedding}, qv, k, {candidate_set: Cs, distance_map: @@dist})` with `Cs = SELECT c FROM TextChunk:c WHERE c.valid_from_epoch <= as_of` returned only eligible chunks at two `as_of` values; distances are cosine **distance** | Only temporal filter for retrieval; the MCP's `search_top_k_similarity` MUST NOT be used for evidence |
| E6 | Query syntax is default **v1**: `FROM Seed:s -(PEdge:e)-> PVert:t WHERE e.epoch <= as_of`. `SYNTAX v3` rejected it. Also confirmed: `VERTEX<T>` parameters (REST `dv=<id>&dv.type=T`), `PRINT expr AS alias` including boolean expressions, `CASE WHEN … END` inside `ACCUM`, `e.attr` on **reverse** edges, `INTERPRET QUERY () FOR GRAPH g {…}` for one-off checks | Do not write `SYNTAX v3`; installs are per query (≈ 30–40 s each) — **install one query per request**: a four-query `INSTALL QUERY` on an 800,000-vertex graph did not finish and was aborted |
| E7 | Accumulators (`SetAccum`, `SumAccum`, `MapAccum`, `MaxAccum`, vertex `@x`, `POST-ACCUM`) run; no `BREAK` in `ACCUM` | Two-step pre-count hub guard (§5.2) |
| E8 | Loading: `CREATE LOADING JOB`, `DEFINE FILENAME`, `LOAD … USING SEPARATOR=",", EOL="\n", HEADER="false"[, VERTEX_MUST_EXIST="true"]`; upload `POST /restpp/ddl/<graph>?tag=<job>&filename=<var>&sep=%2C&eol=%0A`. Loaded and verified: the real **49-column Transaction** row (empty strings for missing DOUBLE/STRING fields, `YYYY-MM-DD HH:MM:SS` datetimes, `true/false` booleans, `UINT` primary id), Card, MADE, DeviceProfile, FROM_DEVICE. `to_datetime()` / `gsql_to_bool()` inside `LOAD` cause parse errors | Stage plain values; `VERTEX_MUST_EXIST="true"` on all edge loads |
| E9 | Counts: `POST /restpp/builtins/<graph>` `{"function":"stat_vertex_number","type":"*"}` / `stat_edge_number`; GET rejected. **The statistic lags**: after a 360,000-row load it read 260,386 and then converged (a later 800,000-row load read 200,000 → 800,000 within ~10 s); no rows were lost (all `validLine` counts matched) | Poll until two consecutive identical readings; the installed-query exact count (`S.size()`) was not verified (install aborted) |
| E10 | GSQL over REST: `POST /gsql/v1/statements`, `Content-Type: text/plain`; tokens: `POST /gsql/v1/tokens {"secret": …}`; the secret is not a Bearer token | Credential variable `TG_SECRET` (§11) |
| E11 | `DROP GRAPH g` fails when the graph has jobs/queries; `DROP GRAPH g CASCADE` works (may report "other operation is running, try to lock the catalog" while a job is still pending, then complete) | CASCADE only for our own throwaway graphs, never `Transaction_Fraud` |
| E12 | **Reserved words (scan of 75 attribute names and 39 type names):** exactly two are reserved — **vertex type `Case`** and **attribute `proxy`**. Every other planned name is valid, including `Transaction`, `Card`, `NEXT`, `DESCRIBES`, `role`, `status`, `text`, `method`, `score`, `channel`, `epoch`, `amount`, `id_15`, `os`, `browser`, `screen` | **`Case` → `FI_Case`; `proxy` → `proxy_type`** (Transaction and FROM_DEVICE). `FI_Case` was created successfully. All other names stay |
| E13 | **Name collisions with the existing graph's global types are harmless:** local `Card` and `DESCRIBES` (global `Card`, `DESCRIBES` exist in `Transaction_Fraud`) were created; `Transaction_Fraud` and the global vertex/edge lists were byte-identical afterwards | No `FI_` prefix needed except `FI_Case` |
| E14 | **Primary-key attribute:** without the option `s.id` is **not** readable (`TYP-158` type-check error); with `ADD VERTEX … WITH primary_id_as_attribute="true"` it is (`R.size() = 1`), and the real `Customer` key `s.customer_id` is readable in a `WHERE` | Declare **every** vertex `WITH primary_id_as_attribute="true"` (accepted by `ADD VERTEX`) |
| E15 | **Load size:** 200,000 real-width (49-column) Transaction rows in one POST (34.6 MB) accepted, 8.6 s (≈ 23,000 rows/s); 10k/50k/100k rows: 7.6/4.0/5.0 s; 360,000 MADE edges (14.2 MB) 5.3 s; narrow 200,000-row and **600,000-row** bodies (10 MB / 31 MB) accepted in 8.9 s / 9.6 s (800,000 vertices stored). A single full-width 590,742-row body (~100 MB) was **not** tested | Load in chunks of ≤ 200,000 rows (retry granularity, keeps bodies ≤ ~35 MB); full graph load is minutes, not hours |
| E16 | **Hub pre-count performance (two-step guard, REST round trip):** device with 80 edges 826 ms; 3,648 edges (null-profile size, 1,001 customers) median 983 ms, max 1,658 ms; 20,000 edges (1,500 customers) 780 ms; 100,000 edges (2,000 customers) 1,033 ms. Correct results (`is_hub` true above `max_c`, as-of filter returned exactly 41 of 80 edges at the cut) | Pre-count is acceptable even for the largest hub; cost is dominated by a **~0.8 s fixed gateway latency per call**. Latency budget: every agent tool call ≥ 0.8 s (`latency_s` is scored) → batch related lookups into one installed query, skip the pre-count for snapshot-blocked entities anyway |
| E18 | **Missing values:** no null for DOUBLE/INT. Verified convention (staged and read back): missing strings `""`; missing `addr1/addr2` `-1`; missing DOUBLE = per-column sentinel from `manifest.json`: `-1` for `dist1`, `dist2`, `d1`, `d2`, `d3`, `d5`, `d10`, `v51`, `v52`, `v79`, `v93`, `v94`, `v217`, `v258`, `v264`, `v308`, **`d4 = -123`, `d15 = -84`** (negative minima), `Card.card2/card3/card5 = -1` | Queries and the agent MUST treat sentinel values as missing (never as data); `stage.py` records them |
| E17 | Auth/secret hygiene: `SHOW USER` / `SHOW SECRET` return partially masked secrets; secret was rotated 2026-09-19 | Print counts/aliases only |

### 2.1 Schema DDL (exact; local schema, TigerGraph 4.2.5)

Run in this order, each `RUN SCHEMA_CHANGE JOB` finishing before the next (E3). The base job's statements were executed successfully, one type per job, in a throwaway graph (Phase 8 step 0); the vector job and index jobs were exercised separately. **Implementation note:** the probe created each type in its own job (vertices first, then edges). Phase 8 should run `fi_base` as **one job per vertex type and per edge type** (the verified form) rather than one large job; the statements are identical.

```gsql
CREATE GRAPH FraudInvestigation ()

CREATE SCHEMA_CHANGE JOB fi_base FOR GRAPH FraudInvestigation {
  ADD VERTEX Customer   (PRIMARY_ID customer_id STRING, card1 INT, n_cards INT, first_epoch INT) WITH primary_id_as_attribute="true";
  ADD VERTEX Card       (PRIMARY_ID card_id STRING, customer_id STRING, card_k INT, card4 STRING, card6 STRING,
                         card2 DOUBLE, card3 DOUBLE, card5 DOUBLE, is_null_type BOOL, first_epoch INT) WITH primary_id_as_attribute="true";
  ADD VERTEX Transaction(PRIMARY_ID txn_id UINT, epoch INT, ts DATETIME, amount DOUBLE, product_cd STRING, channel STRING,
                         risk_score FLOAT, card_id STRING, customer_id STRING, addr1 INT DEFAULT -1, addr2 INT DEFAULT -1,
                         dist1 DOUBLE, dist2 DOUBLE, has_identity BOOL, id_15 STRING, proxy_type STRING, device_type STRING,
                         p_email STRING, r_email STRING,
                         c1 DOUBLE, c2 DOUBLE, c4 DOUBLE, c8 DOUBLE, c10 DOUBLE,
                         d1 DOUBLE, d2 DOUBLE, d3 DOUBLE, d4 DOUBLE, d5 DOUBLE, d10 DOUBLE, d15 DOUBLE,
                         m1 STRING, m2 STRING, m3 STRING, m4 STRING, m5 STRING, m6 STRING, m7 STRING, m8 STRING, m9 STRING,
                         v51 DOUBLE, v52 DOUBLE, v79 DOUBLE, v93 DOUBLE, v94 DOUBLE, v217 DOUBLE, v258 DOUBLE, v264 DOUBLE, v308 DOUBLE) WITH primary_id_as_attribute="true";
  ADD VERTEX DeviceProfile(PRIMARY_ID device_id STRING, profile_str STRING, device_info STRING, os STRING, browser STRING,
                         screen STRING, n_known INT, is_null_profile BOOL, first_epoch INT, snap_customers INT, snap_class STRING) WITH primary_id_as_attribute="true";
  ADD VERTEX EmailDomain(PRIMARY_ID domain STRING, is_anonymizer BOOL, snap_customers INT, snap_class STRING) WITH primary_id_as_attribute="true";
  ADD VERTEX BillingRegion(PRIMARY_ID addr1 UINT, country INT, snap_customers INT, snap_class STRING) WITH primary_id_as_attribute="true";
  ADD VERTEX ClosedCase (PRIMARY_ID case_id STRING, customer_id STRING, card_id STRING, opened_at DATETIME, closed_at DATETIME,
                         open_epoch INT, close_epoch INT, outcome STRING, pattern STRING, n_txns INT, exposure_usd DOUBLE,
                         report_filed BOOL, actions_taken STRING, first_fraud_txn_id INT DEFAULT 0, analyst_notes STRING,
                         device_text STRING) WITH primary_id_as_attribute="true";
  ADD VERTEX FI_Case    (PRIMARY_ID graph_case_id STRING, source_case_id STRING, as_of_epoch INT, as_of DATETIME, status STRING,
                         verdict STRING, fraud_probability DOUBLE, pattern STRING, pattern_description STRING,
                         first_suspicious_txn_id INT DEFAULT 0, exposure_usd DOUBLE, summary STRING, initial_actions STRING,
                         final_actions STRING, what_changed STRING, evidence_json STRING, evidence_requests_json STRING,
                         sar_file BOOL, sar_json STRING, stop_reason STRING, revision INT, created_at DATETIME, updated_at DATETIME) WITH primary_id_as_attribute="true";
  ADD VERTEX TextChunk  (PRIMARY_ID chunk_id STRING, doc_type STRING, source_ref STRING, text STRING, valid_from_epoch INT,
                         pattern STRING, outcome STRING, channel STRING) WITH primary_id_as_attribute="true";                     -- embedding added by fi_vec (E4)

  ADD DIRECTED EDGE OWNS               (FROM Customer, TO Card)                                                   WITH REVERSE_EDGE="OWNED_BY";
  ADD DIRECTED EDGE MADE               (FROM Card, TO Transaction, epoch INT, amount DOUBLE, channel STRING)      WITH REVERSE_EDGE="MADE_BY";
  ADD DIRECTED EDGE NEXT               (FROM Transaction, TO Transaction, gap_s INT)                              WITH REVERSE_EDGE="PREV";
  ADD DIRECTED EDGE FROM_DEVICE        (FROM Transaction, TO DeviceProfile, epoch INT, id_15 STRING, proxy_type STRING, customer_id STRING)
                                                                                                                WITH REVERSE_EDGE="DEVICE_OF";
  ADD DIRECTED EDGE SEEN_ON            (FROM DeviceProfile, TO Card, first_epoch INT)                             WITH REVERSE_EDGE="SEEN_BY";
  ADD DIRECTED EDGE PURCHASER_EMAIL    (FROM Transaction, TO EmailDomain, epoch INT)                              WITH REVERSE_EDGE="PURCHASER_OF";
  ADD DIRECTED EDGE RECIPIENT_EMAIL    (FROM Transaction, TO EmailDomain, epoch INT)                              WITH REVERSE_EDGE="RECIPIENT_OF";
  ADD DIRECTED EDGE BILLED_IN          (FROM Transaction, TO BillingRegion, epoch INT)                            WITH REVERSE_EDGE="BILLED_HERE";
  ADD DIRECTED EDGE CLOSED_ON_CARD     (FROM ClosedCase, TO Card)                                                 WITH REVERSE_EDGE="CARD_HAS_CLOSED";
  ADD DIRECTED EDGE CLOSED_ON_CUSTOMER (FROM ClosedCase, TO Customer)                                             WITH REVERSE_EDGE="CUSTOMER_HAS_CLOSED";
  ADD DIRECTED EDGE CLOSED_INVOLVES    (FROM ClosedCase, TO Transaction, role STRING, is_first BOOL)              WITH REVERSE_EDGE="INVOLVED_IN_CLOSED";
  ADD DIRECTED EDGE CLOSED_CONNECTED_TO(FROM ClosedCase, TO Card)                                                 WITH REVERSE_EDGE="CONNECTED_FROM_CLOSED";
  ADD DIRECTED EDGE CASE_ON_CARD       (FROM FI_Case, TO Card)                                                       WITH REVERSE_EDGE="CARD_HAS_CASE";
  ADD DIRECTED EDGE CASE_TXN           (FROM FI_Case, TO Transaction, role STRING)                                   WITH REVERSE_EDGE="TXN_IN_CASE";
  ADD DIRECTED EDGE CASE_CONNECTED_TO  (FROM FI_Case, TO Card, via STRING)                                           WITH REVERSE_EDGE="CONNECTED_FROM_CASE";
  ADD DIRECTED EDGE CASE_CITES_DEVICE  (FROM FI_Case, TO DeviceProfile)                                              WITH REVERSE_EDGE="DEVICE_CITED_BY";
  ADD DIRECTED EDGE SIMILAR_CASE       (FROM FI_Case, TO ClosedCase | FROM FI_Case, TO FI_Case, score DOUBLE, method STRING, reason STRING)
                                                                                                                WITH REVERSE_EDGE="SIMILAR_FROM";   -- confirmed (E2)
  ADD DIRECTED EDGE DESCRIBES          (FROM TextChunk, TO ClosedCase | FROM TextChunk, TO FI_Case)                  WITH REVERSE_EDGE="DESCRIBED_BY";   -- confirmed (E2, E13)
}
RUN SCHEMA_CHANGE JOB fi_base

CREATE SCHEMA_CHANGE JOB fi_vec FOR GRAPH FraudInvestigation {
  ALTER VERTEX TextChunk ADD VECTOR ATTRIBUTE embedding(DIMENSION=<d>, METRIC="COSINE");   -- <d> fixed by the embedding model (blocker B5)
}
RUN SCHEMA_CHANGE JOB fi_vec

-- one job per index, run sequentially (E3); INT/STRING/DATETIME vertex attributes only
--   Transaction.epoch, ClosedCase.close_epoch, FI_Case.as_of_epoch, TextChunk.valid_from_epoch, DeviceProfile.profile_str  (existence verified via LS; speed unproven, E3)
CREATE SCHEMA_CHANGE JOB fi_idx_txn_epoch FOR GRAPH FraudInvestigation { ALTER VERTEX Transaction ADD INDEX idx_txn_epoch ON (epoch); }
RUN SCHEMA_CHANGE JOB fi_idx_txn_epoch
-- (analogous jobs: idx_cc_close, idx_case_asof, idx_chunk_valid, idx_dev_profile)
```
No `CREATE GRAPH … (*)`, no global `CREATE VERTEX/EDGE`, no `embedding LIST<FLOAT>`, no vertex named `Case`, no attribute named `proxy`. The primary key of every vertex is readable in queries under its declared name (E14). The vector job and the per-index jobs are the only pieces not yet executed together with the final DDL (`<d>` is unknown until B5).

`FROM_DEVICE.customer_id` is deliberate denormalisation so device→customer counting needs no second hop. `Transaction.risk_score`, `m1…m9`, `v*`, `dist*` are stored as context only.

**Not created in CORE:** DeviceModel, Document, Cluster, Merchant, Account, ProductCode vertices; `IN_CLUSTER`, `OF_MODEL`, `HAS_CHUNK` edges (ADVANCED, only after §13 acceptance passes). **Never created:** any vertex/edge/attribute carrying `label`, `is_fraud`, `outcome`, or `pattern` on Transaction/Card/Customer/DeviceProfile/Region/Email (labels exist only on ClosedCase).

### 2.2 Vertex field sources

| Vertex | Field | Source (file.column / derivation) | Time class | Evidence |
|---|---|---|---|---|
| Customer | `customer_id`, `card1` | transactions | static | Y (id) / N |
| | `n_cards` | count distinct derived cards | static | C |
| | `first_epoch` | min `TransactionDT` | valid-from | C |
| Card | `card_id`, `card_k` | derived (§1) | static | Y |
| | `card4`, `card6`, `card2`, `card3`, `card5` | transactions (`card6` '' if null; card2/3/5 = modal value) | static | C |
| | `is_null_type` | `card6` null | static | C |
| | `first_epoch` | min epoch on card | valid-from | C |
| Transaction | `txn_id`, `epoch`, `ts`, `amount`, `product_cd`, `channel`, `card_id`, `customer_id` | TransactionID, TransactionDT, ts, TransactionAmt, ProductCD, channel, derived, customer_id | static | Y |
| | `risk_score` | risk_score | static | **C** (see §6.3) |
| | `addr1`, `addr2` (−1 if null) | addr1, addr2 | static | Y (home/not-home) / C |
| | `dist1`, `dist2`, `d*`, `c*`, `m*`, `v*` | same names | static | C (unnamed model features; say so) |
| | `has_identity`, `id_15`, `proxy_type`, `device_type` | identity.id_15, id_23, DeviceType (`proxy_type` '' if null) | static | Y (`id_15`, `proxy_type` only through §6 rules) |
| | `p_email`, `r_email` | P_/R_emaildomain | static | C |
| DeviceProfile | `device_id`, `profile_str`, `device_info`, `os`, `browser`, `screen`, `n_known`, `is_null_profile` | derived from identity (§1) | static | Y (id, profile_str) |
| | `first_epoch` | min epoch | valid-from | C |
| | `snap_customers`, `snap_class` | distinct customers among txns with `epoch < HIST_END`; class §5.2 | snapshot | **G** |
| EmailDomain | `domain`, `is_anonymizer` (= `anonymous.com`) | P_/R_emaildomain | static | C |
| | `snap_customers`, `snap_class` | as above | snapshot | G |
| BillingRegion | `addr1`, `country` (modal addr2) | transactions | static | Y (as home/not-home) |
| | `snap_customers`, `snap_class` | as above | snapshot | G |
| ClosedCase | 15 CSV fields + `open_epoch`, `close_epoch`, `device_text` | closed_cases_history.csv; `device_text` = regex `came from (a\|an )?(.*?)\. ` on `analyst_notes` (38.9% non-empty) | visible iff `close_epoch ≤ as_of` | Y |
| FI_Case ("Case") | see DDL | agent-written | visible iff `as_of_epoch ≤ current as_of` | Y (as memory) |
| TextChunk | see DDL | §10 | visible iff `valid_from_epoch ≤ as_of` | Y (document) |

### 2.3 Edge field sources and rules

| Edge | Built from | Fields | Time visibility |
|---|---|---|---|
| OWNS | derived card_id | — | static |
| MADE | `card_id` × txn | `epoch`, `amount`, `channel` | `epoch ≤ as_of` |
| NEXT | per `card_id`, ordered by (`epoch`, `txn_id`), consecutive pairs | `gap_s` = epoch(to) − epoch(from) (backward gap only) | `epoch(to) ≤ as_of` |
| FROM_DEVICE | identity join | `epoch`, `id_15`, `proxy_type` ('' if null), `customer_id` | `epoch ≤ as_of` |
| SEEN_ON | distinct (device, card) over FROM_DEVICE | `first_epoch` only — **no last_seen, no counter** | `first_epoch ≤ as_of` |
| PURCHASER_EMAIL / RECIPIENT_EMAIL / BILLED_IN | P_/R_emaildomain, addr1 (non-null only) | `epoch` | `epoch ≤ as_of` |
| CLOSED_ON_CARD / CLOSED_ON_CUSTOMER | closed_cases | — | source ClosedCase visible (§4) |
| CLOSED_INVOLVES | `txn_ids`; `role` = `fraud` (confirmed) or `alert` (cleared); `is_first` = `first_fraud_txn_id` | as stated | same |
| CLOSED_CONNECTED_TO | `connected_card_ids` (92 pairs, 4 cases) | — | same |
| CASE_* / SIMILAR_CASE / DESCRIBES | agent / chunker | as DDL | same as source Case / chunk |

### 2.4 Expected object counts (load verification, exact)

| Object | Count | | Edge | Count |
|---|---|---|---|---|
| Customer | 13,553 | | OWNS | 14,317 |
| Card | 14,317 (672 `is_null_type`) | | MADE | 590,742 |
| Transaction | 590,742 | | NEXT | 576,425 |
| DeviceProfile | 9,706 (incl. the all-null profile: 3,648 txns) | | FROM_DEVICE | 144,432 |
| EmailDomain | 60 | | SEEN_ON | 68,013 |
| BillingRegion | 332 | | PURCHASER_EMAIL / RECIPIENT_EMAIL | 496,262 / 137,453 |
| ClosedCase | 5,565 (4,665 confirmed / 900 cleared) | | BILLED_IN | 525,003 |
| TextChunk (CORE) | 5,565 closed-case + policy/pattern chunks (§10) | | CLOSED_INVOLVES / _ON_CARD / _ON_CUSTOMER / _CONNECTED_TO | 14,955 / 5,565 / 5,565 / 92 |

---

### 2.6 Load status (2026-09-19)
`FraudInvestigation` is loaded with the full dataset except `TextChunk`/`FI_Case` (empty) and the vector attribute (not yet added): every vertex and edge count equals §2.4 and 16 read-back checks pass (`docs/tigergraph_environment.md` §7.4). Sentinel convention: E18.

### 2.5 Production schema decisions (frozen for Phase 8)

| # | Decision |
|---|---|
| 1 | Graph `FraudInvestigation`, **local schema** (`CREATE GRAPH FraudInvestigation ()`); never `(*)`; `Transaction_Fraud` is never referenced by any statement except read-only `SHOW`. |
| 2 | Vertex types: `Customer`, `Card`, `Transaction`, `DeviceProfile`, `EmailDomain`, `BillingRegion`, `ClosedCase`, **`FI_Case`**, `TextChunk` — each `WITH primary_id_as_attribute="true"`. `txn_id` and `BillingRegion.addr1` are `UINT` primary ids; all other keys `STRING`. |
| 3 | Attribute renames: `proxy` → **`proxy_type`** (Transaction, FROM_DEVICE); everything else as §2.1. |
| 4 | Edge types exactly as §2.1 with reverse edges; `SIMILAR_CASE` and `DESCRIBES` are multi-pair edges with reverse edges (confirmed). Case-side edges keep the `CASE_*` names (not reserved). |
| 5 | Labels only on `ClosedCase`; visibility `close_epoch ≤ as_of`; no label/outcome attribute elsewhere (T4/T7). |
| 6 | Temporal filtering on **edge `epoch`** with INT comparisons; vertex indexes created on `Transaction.epoch`, `ClosedCase.close_epoch`, `FI_Case.as_of_epoch`, `TextChunk.valid_from_epoch`, `DeviceProfile.profile_str` (existence verified; performance not relied upon). |
| 7 | Vector: `embedding` attribute on `TextChunk` (COSINE, HNSW default), added after the base job; retrieval via `vectorSearch … candidate_set` built from `valid_from_epoch ≤ as_of` (and `channel`). |
| 8 | Queries: v1 syntax, one `CREATE OR REPLACE QUERY` + `INSTALL QUERY` per request; hub guard = two-step pre-count; vertex-typed parameters for entity seeds. |
| 9 | Loading: staged CSV → loading jobs (`HEADER="false"`, `VERTEX_MUST_EXIST="true"` on edges) → `POST /restpp/ddl`, chunks ≤ 200,000 rows; counts polled until stable. |
| 10 | Connection: `.env` `TG_HOST`, `TG_TGCLOUD=true`, `TG_SSL_PORT=443`, `TG_GRAPHNAME=FraudInvestigation`, `TG_SECRET`; tooling aborts on any other graph name. |

---

## 3. Ingestion specification

Python (`pandas`, chunked) writes staged CSVs to `data/staged/` (git-ignored); TigerGraph loading jobs receive them through `POST /restpp/ddl/<graph>?tag=<job>&filename=<var>` (E8, verified with a local CSV body; S3/GCS not probed). Steps are deterministic and idempotent (upsert on primary IDs). Code MUST reuse `scripts/analysis/common.py` (`derive_card_ids`, device string, epoch) — do not re-implement.

| Step | Output | Rule |
|---|---|---|
| 1 | `customer.csv`, `card.csv`, `region.csv`, `email.csv`, `device.csv` | fields §2.2; hub snapshot fields from `epoch < HIST_END` |
| 2 | `transaction.csv` | column subset of §2.1; **no label columns** |
| 3 | `owns`, `made`, `next`, `from_device`, `seen_on`, `purchaser_email`, `recipient_email`, `billed_in` | §2.3 |
| 4 | `closed_case.csv`, `closed_on_card`, `closed_on_customer`, `closed_involves`, `closed_connected_to` | `open_epoch/close_epoch` from timestamps; `role`, `is_first`, `device_text` |
| 5 | `text_chunk.csv` (+ embeddings) | §10 |
| 6 | `case_pack` stays **outside** the graph (runner input) | `as_of = opened_at` |

Load order: Customer, Card, BillingRegion, EmailDomain, DeviceProfile → Transaction → transaction edges → ClosedCase → closed edges → TextChunk (+ vectors). After load, §13-A tests MUST pass before anything else is built.

Loading rules (E8/E9/E18): one loading job (`fi_load`) with a `DEFINE FILENAME`/`LOAD` per staged file; headerless files, `HEADER="false"`, `SEPARATOR=","`, `EOL="
"`, `QUOTE="double"` (closed-case notes contain commas); edge loads use `VERTEX_MUST_EXIST="true"`; timestamps as `YYYY-MM-DD HH:MM:SS`, booleans as `true`/`false`, missing `addr1/addr2` as `-1`; **no** token functions such as `to_datetime()`/`gsql_to_bool()` inside `LOAD`; upload in chunks of ≤ 200,000 rows (verified: 200,000 full-width rows = 34.6 MB in 8.6 s; 600,000 narrow rows also accepted; a single ~100 MB body is untested), so Transaction (590,742 rows) is 3–4 POSTs; verify with `stat_vertex_number`/`stat_edge_number` **polled until two consecutive readings agree** (the statistic lags, E9); vector rows loaded last through the vector LOAD job (E4).

---

## 4. Temporal rules (exact)

**T1 — required parameter.** Every installed query has `INT as_of` with no default; the MCP wrapper (§11) sets it from the case.
**T2 — transaction-level visibility.** A Transaction, and every edge carrying `epoch`, is usable iff `epoch ≤ as_of`. Filters MUST be applied on the *edge* attribute during traversal (`e.epoch <= as_of`), not after fetching vertices.
**T3 — SEEN_ON.** Usable iff `first_epoch ≤ as_of`. Any count/first/last derived from it MUST be recomputed from FROM_DEVICE/MADE edges with `epoch ≤ as_of`.
**T4 — labels.** A closed case, its outcome, pattern, involved txns, connected cards, notes, chunk and vector hit are visible iff `close_epoch ≤ as_of` (**never** `open_epoch`). Using `open_epoch` inflates the low-degree device signal by +29% volume / +4 precision pts (script 04).
**T5 — agent cases.** A `Case` (and its edges/chunks/SIMILAR_CASE) is visible iff `as_of_epoch ≤ current as_of`, where `as_of_epoch` is the case's own `opened_at`. Wall-clock `created_at` is audit-only. The runner MUST process cases in ascending `opened_at`. A later revision of a case is visible only if its `as_of_epoch` is visible; retrieval takes the highest revision. `open`/`escalated` cases are context, never a fraud label.
**T6 — no stored live aggregates.** Vertices MUST NOT store transaction counts, sums, `last_seen`, fraud counts, degrees or shares. Only `snap_*` (computed from `epoch < HIST_END`) and immutable attributes are stored. Live degree/share/counts are computed in the query from visible edges.
**T7 — no labels on facts.** Transaction/Card/Customer/Device/Region/Email carry no outcome. A confirmed-fraud txn is recognised only by traversing `INVOLVED_IN_CLOSED → ClosedCase` with T4.
**T8 — NEXT.** Only backward gaps stored; queries MUST NOT follow NEXT from a txn to a txn with `epoch > as_of`.
**T9 — audit.** Every tool result carries `as_of` and `max_epoch_seen`; the wrapper rejects results where `max_epoch_seen > as_of` (§11). Unlabeled MUST NOT be interpreted as legitimate.
**T10 — inclusive boundary.** `epoch ≤ as_of` (a txn at exactly `as_of` is visible; the benchmark flagged txns are 1–6 h before `opened_at`).

Oracle: `scripts/analysis/asof.py` (`view_txns`, `label_visible`, `dev_stats_oracle`); tests in §13-C compare GSQL output against it.

---

## 5. Hub rules (exact)

Terms: **as-of degree** of an entity = distinct `customer_id`s connected to it through edges with `epoch ≤ as_of` (including the current customer). **Snapshot class** = precomputed from `epoch < HIST_END`, used only as a cheap pre-filter.

### 5.1 Sharing evidence eligibility (device, region, email)

| Entity | As-of degree | Use |
|---|---|---|
| DeviceProfile | `n_known = 0` (all-null profile) | **Blocked**: never linked, never counted (3,648 txns; 1,001 customers at HHG-009 with 33 "fraud customers") |
| | ≤ 5 | Strong sharing evidence (S02a) |
| | 6–20 | Moderate (S02b) |
| | 21–100 | **No sharing evidence** (lift ≈ 1.0). May be considered only by the ring rule (§6, S01) |
| | > 100 | **Blocked** |
| BillingRegion | any | Attribute only (home vs not-home). Region cluster (S03) usable only if as-of degree ≤ 20 **and** ≥ 2 independent confirmed cases; otherwise blocked |
| EmailDomain | any | Attribute only. Recipient-email link (S04) usable only if as-of degree ≤ 20 **and** ≥ 2 independent confirmed cases |
| `addr2` | — | Not a vertex; only "≠ 87" as an anomaly attribute |
| Customer | > 1,000 txns | Behavioural baselines MUST be card-level and windowed (`lookback_days`, default 180) |

`n_known` between 1 and 4 does **not** gate anything (validated: no difference at low degree). Degree is the gate.

### 5.2 Snapshot classes (pre-filter only)

`snap_class` ∈ `NULL` (device `n_known=0`), `HUB` (≥ 100 customers), `MID` (21–99), `LOW` (≤ 20), `UNSEEN` (no txn before `HIST_END`; 2,460 of 7,437 devices at Sep-1→Oct-31 in validation). Regions: `MEGA` ≥ 300, `HUB` 100–299, `MID` 21–99, `LOW` ≤ 20. Emails: `GENERIC` ≥ 50, `RARE` < 50.

**Expansion rule.** A query MUST skip expansion of a vertex if `snap_class ∈ {NULL, HUB, MEGA, GENERIC}` **or** its as-of degree (computed at query time, using the two-step pre-count below, `max_customers` default 100) exceeds `max_customers`. Skipped vertices are returned in `skipped_hubs` (id, reason, as-of degree bound) and MUST NOT appear in `connected_cards` / `connected_device_profiles`. `UNSEEN` and any vertex whose snapshot class is lower than its as-of class are handled by the as-of degree check, not by the snapshot.

**Two-step hub guard (E7, verified pattern).** GSQL has no `BREAK` in `ACCUM`, so hub gating is: (1) traverse the visible edges (`e.epoch <= as_of`) once with `ACCUM t.@in_deg += 1` (or a `SetAccum<STRING>` of customer ids for distinct customers); (2) flag in `POST-ACCUM`: `CASE WHEN t.@in_deg > max_customers THEN t.@is_hub += TRUE END`; (3) expand only `SELECT v FROM Deg:v WHERE v.@is_hub == FALSE` and report the flagged vertices in `skipped_hubs`. The pre-count itself touches every visible edge of the seed entity, so for the null profile (3,648 txns, 1,001 customers) and other hubs the snapshot class (`snap_class`) is checked **first** and the pre-count is skipped for `NULL/HUB/MEGA/GENERIC`. **Performance verified (E16):** the two-step pre-count over 3,648 edges (null-profile size) took a median 983 ms, and over 100,000 edges 1,033 ms, against a ~0.8 s fixed REST latency; the snapshot pre-check is therefore an optimisation, not a necessity.

### 5.3 Hub examples that MUST be reproduced (script 06)
HHG-009 device = null profile (1,001 customers as-of) → blocked. HHG-013 device: 59 customers as-of (would be "MID") vs 253 with future data ("HUB"). HHG-017 device: 164 as-of → blocked. HHG-014 device: 44 as-of, **eligible only via S01**.

---

## 6. Evidence hierarchy (exact)

### 6.1 Tiers, definitions, ratings

Metric basis: Aug–Oct point-in-time; lift vs channel base rate; ratings are for evidence value in a case file (`docs/signal_validation.md` §3).

| Tier | ID | Signal — exact definition (as of `as_of`) | Rating | Use rule |
|---|---|---|---|---|
| 1 | S19 | Customer/step-up response (simulated; §8.4) | HIGH (policy) | Recorded in `evidence_requests` |
| 2 | S01 | **Ring rule.** Device (not `NULL`) with as-of `customers ≥ 3` ∧ `new_share ≥ 0.9` ∧ `proxy_share ≥ 0.5`; `new_share` = share of the device's visible txns with `id_15 = 'New'`, `proxy_share` = share with `proxy_type != ''` (the `id_23` value). No label, no risk score. Alerts suppressed while graph history < 30 days | HIGH | Supports shared origin / connected cards (R6, R9). Does **not** by itself prove an individual customer's txn is fraud (R1 applies) |
| 3a | S02a | Device with as-of degree ≤ 5 on which **another** customer has a confirmed-fraud closed case visible (`close_epoch ≤ as_of`) | HIGH | precision 49.7–51.8% (lift 4.6–4.8) |
| 3b | S02b | Same, degree 6–20 | MEDIUM | precision 24–27% (lift 2.2–2.5) |
| 4 | S06 | **R5 exactly:** ≥ 3 online txns < $5 on the card within 1 h then an online txn ≥ $20 | MEDIUM (policy-defined; statistically unvalidated: 7 hits, 1/16 labelled cases) | Apply R5 as written; do not infer card testing from history |
| 4 | S07 | Burst family: ProductCD C, amount $400–500, ≥ 1 prior same-card txn in 1 h (parameters fitted to 5 cases) | MEDIUM | 33 hits (15 fraud, 0 cleared, 18 unlabeled) |
| 5 | S08 | Online amount z-score > 2 vs the card's own log-amount history (≥ 20 prior txns) | MEDIUM | 251 hits, precision 22.3% (lift 2.1), cleared 0.8% |
| 6 | S05, S09–S16, S18 | Repeat victim; new ProductCD; `id_15 = New`; anonymous/hidden proxy; New+proxy; `addr1` missing; no identity record; region away from PIT modal (in-person); unnamed model features (C/D/V); similar closed cases | LOW | Context only; see §6.2 |
| 7 | S17 | `risk_score` | LOW | Input/triage; §6.3 |
| — | S20 | Hub links (§5) | not evidence | Listed as ignored |

Rejected after validation (MUST NOT be used as evidence): V93/V52/V79 (channel artifact), `id_15 = New` alone (lift 0.74), region "never seen" (lift 0.81), region/email/device sharing at degree > 20.

### 6.2 Stacking rules
1. Tier-6 signals are correlated (identity flags share a cause). They count as **one** independent evidence source together.
2. Independence is judged by *source*: device graph, card sequence (S06/S07), amount history (S08), closed-case history, customer response.
3. The "≥ 2 independent pieces" stopping test (policy §6) requires two sources from tiers 1–5.
4. Tier 6–7 evidence alone MUST NOT raise `fraud_probability` above 0.50 (design guardrail; calibration constants are a Phase-6 decision, see §17-B3).

### 6.3 `risk_score` rules
Stored and returned. MUST NOT be a filter, ranking key, retrieval key, or tool-selection trigger, and MUST NOT be cited as evidence of fraud. It may be cited only as "model score = x, an input". It is blind to the ring (0 of 60 Nov–Dec ring txns ≥ 0.5, mean 0.14) and all cleared alerts score ≥ 0.81.

---

## 7. Investigation queries (exact contracts)

All queries: `as_of INT` required; every returned object satisfies §4; every result includes `as_of`, `max_epoch_seen`; hub rules §5 applied where entities are expanded. Implementation class: **G** = GSQL installed query, **P** = Python, **V** = vector search. "Oracle" = the script that supplies the reference value.

| ID | Name | Class | Inputs | Output | Tier | Oracle / test |
|---|---|---|---|---|---|---|
| Q0 | `get_case_context` | G+P | `source_case_id` (from runner config, not LLM) | flagged txn (all stored fields), card, customer, trigger text, `as_of` | ctx | 06: card_id match 20/20 |
| Q1 | `card_history` | G | `card_id, from_epoch, to_epoch(≤as_of), limit≤500` | txns ordered by epoch with amount, product, channel, addr1, device_id, `id_15`, proxy, `gap_s` | 4 | oracle view; ordering by (epoch, txn_id) |
| Q2 | `card_baseline` | G (+P for percentiles) | `card_id, lookback_days=180` | n, span_days, product histogram, channel mix, addr1 histogram + PIT modal, distinct devices with first_epoch, hour histogram, Σ/Σ²/max/min of log(1+amount), `is_mega_customer` | 4–5 | `signals.card_pit` z, modal region |
| Q3 | `region_novelty` | G | `card_id, txn_id` | txn addr1, PIT modal in-person addr1 (needs ≥ 30 prior in-person), share of history in txn region, home activity continuing?, `addr2 != 87` flag, region `snap_class` | 6 | 07 `away_c` |
| Q4 | `device_neighbors` | G | `device_id, days=30, max_customers=100` | cards/customers on device with epoch window counts, `id_15`, proxy; `skipped_hubs` | 3 | 02 oracle |
| Q5 | `device_profile_stats` | G | `device_id` | as-of `customers, txns, txns_per_customer, new_share, proxy_share, fraud_customers` (customers with a confirmed-fraud txn on device, case closed ≤ as_of, **all** customers — flag own separately), `n_known`, `snap_class`, `eligible_sharing`(§5.1), `ring_suspect`(S01) | 2–3 | 02 (`dev_stats_oracle`), 05 |
| Q6 | `device_fraud_history` | G | `card_id, days=30, max_degree=20` | per device the card used: eligible neighbours' visible closed cases (case_id, pattern, outcome, close_epoch) | 3 | 07 S02 tables |
| Q7 | `burst_detect` | G | `card_id, window_s ∈ {3600,172800}` | counts of txns, online < $5, follow-up ≥ $20, Σ amount, distinct devices/products; `r5_met` boolean per §6.1 S06; `family_hits` (S07) | 4 | 07 R5 / burst |
| Q8 | `prior_cases` | G | `customer_id, card_id` | visible ClosedCase list (id, pattern, outcome, exposure, report_filed, close_epoch), visible agent Cases, counts | ctx | T4 |
| Q9 | `ring_expand` | G | `seed_device_id \| seed_card_id, days, max_customers=100, hops ≤ 2` | 1-hop cards on device; 2-hop their other **eligible** devices and visible closed cases; candidate connected cards/devices with first/last **visible** txn on the profile; `skipped_hubs` | 2–3 | 05 `--as-of`: ring 44 customers at 2016-11-22 20:11:00 |
| Q10 | `device_anomaly_scan` | G | `since_epoch, min_customers=3, new_share≥0.9, proxy_share≥0.5` | ranked suspect devices with Q5 aggregates and `unlabeled_customers` | 2 | 05: ring found; 2 devices at 2016-11-12 and 2016-11-22 |
| Q11 | `similar_cases` | G+V+P | features (channel, pattern hypothesis, device_id, addr1 band, amount band), text | structured candidates (same card/customer/eligible device) fused with vector hits; each labelled `linked` (shares an eligible entity) or `precedent` | ctx | temporal filter test |
| Q12 | `exposure` | G/P | `txn_ids[]` | Σ\|amount\|, n, first/last ts; rejects ids with `epoch > as_of` | — | arithmetic |
| Q13 | `cross_customer_burst_scan` | G | `window_s=3600, min_txns=2, product_cd='C', amt_lo=400, amt_hi=500` | cards/customers with ≥ n matching txns in a window; distinct devices; Σ | 4 | 07 burst family (33 hits Aug–Oct) |
| Q14 | `region_cluster` | G | `addr1, days=14, max_customers=20` | cards with visible confirmed fraud (only if §5.1 allows) | 6 | 07 link table |
| Q15 | `email_link` | G | `domain, days=30, max_customers=20` | same for recipient domain | 6 | 07 link table |
| Q16a | `write_case` | P (upsert) | Case object (§8.6) | creates/updates `FI_Case`; idempotent on `graph_case_id`, `revision+1`; sets `as_of_epoch` from runner | — | §13-H |
| Q16b | `link_case` | G | ids, roles | CASE_* / SIMILAR_CASE / CITES_DEVICE edges; **rejects unknown IDs** | — | §13-H |
| Q16c | `find_cases` | G | `entity_id` (card/customer/device) | visible ClosedCase and agent Cases | ctx | T4/T5 |
| Q17 | `leak_check` | G | `as_of, ids/result` | `max_epoch`, offenders with `epoch > as_of` or `close_epoch > as_of` | — | 03/06 |
| Q18 | `validate_ids` | G | txn/card/customer/device ids | ids missing from the graph | — | answer-file validator |

Query-writing rules (E6/E7/E10): default v1 syntax (no `SYNTAX v3`); edge temporal filter `e.epoch <= as_of` in the `WHERE` of the traversal; INT epoch comparisons only (no `datetime_to_epoch`, no `now()`); `LIST<FLOAT>` parameters passed as repeated query-string parameters; `CREATE OR REPLACE QUERY` + `INSTALL QUERY` per query, installs sequential.

Q11 vector step is exactly: `Cs = SELECT c FROM TextChunk:c WHERE c.valid_from_epoch <= as_of AND c.channel == channel;` then `V = vectorSearch({TextChunk.embedding}, qv, k, {candidate_set: Cs, distance_map: @@dist});` (E5).

Not to be built: a query that ranks or filters by `risk_score`; a query that returns labels for arbitrary transactions; a query that accepts free GSQL.

---

## 8. Policy constraints (machine-checkable)

### 8.1 Action identifiers and approval route (exact)
Actions: `ALLOW_TRANSACTION, DECLINE_TRANSACTION, MONITOR_CARD, MONITOR_CONNECTED_CARDS, WARN_CUSTOMER, VERIFY_WITH_CUSTOMER, STEP_UP_AUTH, BLOCK_CARD, BLOCK_ALL_CARDS, GENERATE_REPORT, CREATE_CASE, FILE_REPORT, ESCALATE_TO_ANALYST, CLOSE_NO_FRAUD`.

| Route | Actions |
|---|---|
| `auto` | ALLOW_TRANSACTION, MONITOR_CARD, MONITOR_CONNECTED_CARDS, WARN_CUSTOMER, VERIFY_WITH_CUSTOMER, STEP_UP_AUTH, GENERATE_REPORT, CREATE_CASE, ESCALATE_TO_ANALYST, CLOSE_NO_FRAUD |
| `L1` | DECLINE_TRANSACTION; BLOCK_CARD when exposure ≤ $2,500 |
| `L2` | BLOCK_CARD when exposure > $2,500; BLOCK_ALL_CARDS (always); FILE_REPORT (always) |

Only `auto` actions may be executed; `L1`/`L2` are recommended with the route and wait for a human. Actions are ordered by what happens first.

### 8.2 Rules as decision constraints
| Rule | Condition | Required actions | Constraint |
|---|---|---|---|
| R1 | single weak signal (incl. risk score alone) and assessed P < 0.70 | VERIFY_WITH_CUSTOMER or STEP_UP_AUTH | BLOCK_* MUST NOT precede verification |
| R2 | customer denies | BLOCK_CARD + CREATE_CASE; + FILE_REPORT if exposure > $1,000 or shared device profile or another card's fraud | route by exposure |
| R3 | customer confirms | CLOSE_NO_FRAUD; note confirmation in case | |
| R4 | no reply in 24 h | MONITOR_CARD + DECLINE_TRANSACTION for pending; escalate if exposure > $500 | |
| R5 | ≥ 3 small online auths on a card within 1 h then a larger purchase | DECLINE_TRANSACTION + STEP_UP_AUTH; BLOCK_CARD if a purchase > $100 already cleared | S06 definition |
| R6 | several cards show fraud from same device / region / recipient email in a window | name the shared element; CREATE_CASE, FILE_REPORT, MONITOR_CONNECTED_CARDS for every sharing card | shared element MUST pass §5 eligibility |
| R7 | dispute matches customer's own recurring pattern (same merchant, amount, monthly) | CREATE_CASE + VERIFY_WITH_CUSTOMER + WARN_CUSTOMER; do not block | **no merchant field exists** (§17-B6) |
| R8 | verdict `uncertain` and exposure > $500, or conflicting evidence | ESCALATE_TO_ANALYST | |
| R9 | fits no known pattern; coordinated/repeated abuse across customers | CREATE_CASE + FILE_REPORT + ESCALATE_TO_ANALYST; describe pattern in own words | pattern `undocumented` |
| R10 | BLOCK_ALL_CARDS | only if ≥ 2 of the customer's cards have confirmed fraud or credentials confirmed compromised | |

### 8.3 Case vs report (§3a)
`CREATE_CASE` when P ≥ 0.30, whenever evidence is requested, or on any customer dispute. `FILE_REPORT` only if fraud confirmed/strongly suspected **and** (exposure > $1,000 ∨ shared device/region cluster/another customer's fraud ∨ coordinated/undocumented). A report always has a case. `sar.file == true` ⇔ `FILE_REPORT ∈ final actions`.

### 8.4 Evidence requests (simulation)
The agent MAY request `customer_validation`, `step_up_auth`, `analyst_info` without approval. Responses do not exist in the data; the system MUST simulate them with a **deterministic, documented policy** (input: case id and a seed, output recorded verbatim in `evidence_requests[].assumed_response`); the simulator MUST NOT read the answer key or any label of the flagged transaction and MUST NOT be tuned on the 20 cases (§17-B4).

### 8.5 Stopping and calibration
Stop when P ≥ 0.85 or ≤ 0.15 with ≥ 2 independent sources (§6.2), or a verification settles it, or further steps are unlikely to change the decision (state in `stop_reason`). Exposure = Σ\|amount\| of `affected_txn_ids` including the flagged one.

### 8.6 Output constraints (`cases/<case_id>.json`)
Fields, enums and nesting exactly as README "Answer Format". Additionally the validator MUST enforce: every ID exists (Q18); legitimate ⇒ `affected_txn_ids=[]`, `exposure_usd=0`, `sar.file=false`; `sar.file=false` ⇒ `narrative=""`, `subjects=[]`, `total_amount_usd=0`, `activity_dates=[]`; `pattern_description` non-empty iff pattern = `undocumented`; `exposure_usd == Σ|amount|` of `affected_txn_ids`; every txn in `affected_txn_ids` has `epoch ≤ as_of`; `final == initial` ⇒ `what_changed == "nothing"`; every action/route pair matches §8.1 (BLOCK_CARD route follows exposure); `tool_calls`, `tokens`, `latency_s` measured, not estimated; `written_to_graph` true only if the Case vertex exists and `graph_case_id` resolves.

---

## 9. What belongs where

| Concern | GSQL | Python (app) | GraphRAG / vector |
|---|---|---|---|
| Time-bounded traversals (card history, windows, device/region/email neighbours, ring expansion, burst scans) | ✔ | | |
| As-of degree, shares, closed-case visibility, hub gating (two-step pre-count) | ✔ | | |
| Card baseline accumulators (n, Σ, Σ², histograms, first-seen sets) | ✔ | z-scores, percentiles, modal region | |
| Prior cases / find cases | ✔ | | |
| Case create/update, edges, ID validation | edge writes & validation ✔ | orchestration, revisions, JSON | |
| Similar cases | structured half ✔ | fusion / ranking | text half ✔ |
| Policy engine R1–R10, routing, next-best-actions, calibration, stopping | | ✔ (deterministic code; LLM does not decide actions) | policy text retrieval by rule id |
| SAR narrative, summary, evidence wording | | LLM with structured evidence | regulatory guidance chunks (ADVANCED) |
| Evidence-request simulator | | ✔ | |
| Answer-file validation | Q18 | ✔ | |
| Monitoring scans (ring, burst) | Q10, Q13 | scheduler, dedup | |
| Graph algorithms (WCC/Louvain, Jaccard) | | | **ADVANCED only**; monitoring output goes to `Cluster`, never read by case investigations |
| Embeddings | | model calls | vector index |

The LLM proposes hypotheses and writes prose; **actions, routes, exposure, IDs, and rule citations are computed by deterministic code from tool results**.

---

## 10. GraphRAG specification

| Doc type | Chunk | `valid_from_epoch` | Metadata |
|---|---|---|---|
| `closed_case` (5,565) | one per case: `analyst_notes` prefixed with pattern/outcome/channel/exposure band | `close_epoch` | pattern, outcome, channel, device_text |
| `policy` | one per action (14), route table, rules R1–R10, §3a, §3b, §4–§7 | 0 | rule id in `source_ref` |
| `pattern` | five patterns + "not exhaustive" | 0 | |
| `format` | answer-format field rules (SAR who/what/when/where/how/why) | 0 | |
| `regulatory` (ADVANCED) | ~400-token sections of the README-listed PDFs | 0 | url, section |
| `case` (ADVANCED) | agent Case summary | case `as_of_epoch` | |

Retrieval (`similar_cases`): (1) structured seeds — same card, same customer, same **eligible** device, visible at `as_of`; (2) vector top-k (k ≤ 20) over chunks with `valid_from_epoch ≤ as_of` and same `channel`; (3) expand each hit `DESCRIBES → ClosedCase → INVOLVES → Transaction → FROM_DEVICE`; label `linked` only if it shares an eligible entity, else `precedent`. Policy chunks are fetched by exact `source_ref` for citations. Retrieval keys MUST NOT include `risk_score`. Closed-case notes are templated (392 normalised templates) and pattern is structurally determined, so they are base-rate and wording context, never labels.
Vector attributes and `vectorSearch` with `candidate_set` are **verified on this workspace** (E4/E5); the external-store fallback is no longer needed. The embedding `DIMENSION` is fixed at schema time and cannot be changed without dropping the attribute, so choose the embedding model (blocker B5) before running `fi_vec`.

---

## 11. TigerGraph MCP exposure

Two layers: `tigergraph-mcp` **1.0.3** (69 tools, names in `docs/tigergraph_environment.md` §3.2; not yet installed here) and a thin **fraud-tools wrapper** (the only interface the LLM sees).

Connection settings (`.env`, git-ignored): `TG_HOST`, `TG_TGCLOUD=true`, `TG_SSL_PORT=443`, `TG_GRAPHNAME=FraudInvestigation`, and **`TG_SECRET`** (database secret; the MCP passes it as `gsqlSecret` and mints tokens itself). `TG_API_TOKEN`/`TG_JWT_TOKEN` are for ready-made tokens and MUST NOT hold the secret. The workspace's existing graph `Transaction_Fraud` MUST NOT be the default graph of any MCP instance, and the build tooling refuses writes when `graph_name == Transaction_Fraud`.

Instances: (a) **build** instance, full privilege, used only by developer scripts; (b) **agent-path** instance started with `--allowed-tools read-only` **plus** the wrapper allow-list `run_installed_query`, `get_vertex_count`, `get_edge_count` and the single write path implemented by the wrapper for `Case`-family objects (via installed queries / upserts on `Case`, `CASE_*`, `SIMILAR_CASE`, `DESCRIBES`); `--blocked-tools destructive` always. `tigergraph__gsql`, `run_query`, `search_top_k_similarity`, `drop_*`, `clear_graph_data` are never reachable from the agent path.

Wrapper responsibilities: (1) hold `case_id → as_of` from `case_pack.csv`; (2) inject `as_of` into every query, never accepting it from the model; (3) enforce parameter caps (`limit ≤ 500`, `hops ≤ 2`, `max_customers ≤ 100`, window ≤ 30 days); (4) call `leak_check`/`max_epoch_seen ≤ as_of` on every result and drop violations; (5) strip or block fields in §12; (6) count `tool_calls`, tokens, latency per case; (7) validate IDs before any write.

Agent-facing tools (names fixed): `get_case_context`, `card_history`, `card_baseline`, `region_novelty`, `device_profile`, `device_neighbors`, `device_fraud_history`, `burst_check`, `prior_cases`, `expand_ring`, `scan_suspicious_devices`, `scan_bursts`, `region_cluster`, `email_link`, `similar_cases`, `retrieve_policy`, `exposure`, `validate_ids`, `write_case`, `find_cases`. All read-only except `write_case`, which only writes `Case`-family vertices/edges and is the sole write path.

---

## 12. What MUST NEVER be exposed to the agent

1. **`as_of`, `from_epoch`/`to_epoch` beyond `as_of`, or any way to read after `as_of`** — the model supplies neither the case time nor a window end.
2. **Free-form GSQL / Cypher / schema / loading tools**, `leak_check` internals, and the raw `tigergraph-mcp` tool set.
3. **Labels for transactions outside the visible closed cases**; any `is_fraud`/`outcome` attribute (none exist by design).
4. **`risk_score` as a parameter** of any tool, or any tool whose result is ordered by it.
5. **Seed/generation artefacts**: `TransactionDT mod 60`, minute-resolution flags, the C-vector template, identity of seeded rows.
6. **Snapshot hub classes as evidence** (`snap_class`, `snap_customers`) — exposed only inside `skipped_hubs` reasons.
7. **Benchmark-specific analysis**: `docs/investigation_findings.md` §3, §5, §7, `docs/signal_validation.md`, `docs/graph_architecture.md`, `scripts/analysis/output/06_benchmark_leakage.*`, `DATASET_SPEC.md`, and any file naming HHG cases with data facts. The agent's prompt/knowledge base contains only: README pattern text, the fraud policy, the answer format, regulatory documents, and the closed-case chunks (visible per T4). Hard-coding the ring device string, the burst fingerprint, or per-case facts anywhere in the agent is prohibited; discovery must occur through queries Q5/Q9/Q10/Q13.
8. **Original public IEEE-CIS/Kaggle data** — MUST NOT be loaded or referenced (disqualification).
9. **Future agent cases** (`as_of_epoch > current as_of`), including other `HHG` outputs from the same run.
10. **The simulator's rules** for assumed customer responses (the agent receives only the response text).
11. **Internal counters** such as `future_hidden`, full-data degrees, end-of-window degrees.

---

## 13. Acceptance tests (per component; must pass before the next component starts)

Reference scripts are in `scripts/analysis/`; "expected" values are regression targets.

**A0. Environment / schema safety** — items 1–2 and 5 were **executed in Phase 8 step 0** on throwaway graphs; items 3–4 and the re-checks are the acceptance gate for creating the real graph.
1. ✔ Local `Card`, `DESCRIBES` (and every other planned name) created; `Case` and `proxy` are reserved (E12, E13). Throwaway graphs dropped with `CASCADE`.
2. ✔ `SHOW GRAPH *` shows `Transaction_Fraud` byte-identical and the global vertex/edge lists unchanged before and after all probes.
3. After the schema jobs, `SHOW GRAPH *` lists `FraudInvestigation` with all §2.1 types (incl. `FI_Case`); no global type added; `LS` lists the five indexes.
4. The build tooling aborts if `TG_GRAPHNAME` is `Transaction_Fraud` or empty, and never issues a statement containing `Transaction_Fraud` other than read-only `SHOW`.
5. ✔ No secret or token appears in any log or tool output (regex scan for the secret value and `eyJ…` tokens).
6. Primary keys: for every vertex type, `WHERE s.<key> == "<x>"` compiles (E14).

**A. Ingestion / schema**
1. Counts equal §2.4 exactly (vertices and edges), measured with `stat_vertex_number` / `stat_edge_number` (E9); vectors present for all TextChunk vertices (`get_vector_index_status` or a `vectorSearch` smoke test).
2. `card_id` of the 20 flagged txns equals case-pack `card_id` (20/20); 14,955/14,955 closed-case txns map to their `card_id` (script 01).
3. No attribute named `label|outcome|pattern|is_fraud` exists on Transaction/Card/Customer/DeviceProfile/Region/Email.
4. `NEXT` count = txns − cards; every NEXT gap ≥ 0; no NEXT crosses cards.
5. Ring profile has 114 txns / 52 customers / 2 waves; all-null profile has 3,648 txns.

**B. Card identity** — script 01 checks (6/6) re-run against the loaded graph via Q0/Q1: `card_id` of every txn in Card→MADE equals the derived key.

**C. Temporal (highest priority)**
1. For 400 random (device, time) points GSQL Q5 output equals `dev_stats_oracle` (`customers, txns, new, proxy, fraud_customers`) — script 02.
2. Future-corruption invariance: after randomising devices/customers/identity/labels of all rows with `epoch > as_of` (and flipping labels of cases closing after `as_of`), Q1/Q4/Q5/Q9 return byte-identical results — script 03 pattern; the same test MUST fail on a deliberately unfiltered query.
3. Label gating uses `close_epoch`: the transaction-level S02 count (as-of degree ≤ 20, `n_known ≥ 3`, another customer's visible confirmed fraud, Aug–Oct data) is 918 flagged / 289 fraud; the `open_epoch` variant returns 1,180 (script 04) — the build MUST reproduce 918.
4. For all 20 benchmark `as_of` values every tool result has `max_epoch_seen ≤ as_of` and every visible closed case has `close_epoch ≤ as_of` (script 06: 20/20).
5. Agent-case memory: run the 20 cases in `opened_at` order; no retrieved `Case` has `as_of_epoch > current as_of`; shuffling run order does not change any case's retrieved memory.

**D. Hub rules**
1. Q4/Q9 never return the null profile, any device with as-of degree > 100, MEGA/HUB regions, or GENERIC emails as neighbours; they appear in `skipped_hubs`.
2. HHG-009 device (null) → blocked; HHG-013 device → 59 as-of (not 253); HHG-017 → blocked; HHG-014 device → 44 as-of and `ring_suspect = true`.
3. Sharing evidence appears only for as-of degree ≤ 20; at 21–100 Q6 returns no evidence (S02c).
4. Snapshot drift: a device with `snap_class = LOW` whose as-of degree is 30 at `as_of` is treated as > 20.

**E. Query-by-query**
| Query | Test |
|---|---|
| Q0 | Returns flagged txn/card/customer/trigger for all 20; `as_of == opened_at` epoch |
| Q1/Q2 | Equal `card_pit` values (`hist`, `z`, modal region) on 1,000 random txns |
| Q3 | `away` flag equals `away_c` from script 07 |
| Q5 | §C.1; ring: 24 customers @ 2016-11-12 00:46:24, 44 @ 2016-11-22 20:11:00, both `new_share = proxy_share = 1.0` |
| Q7 | `r5_met` true for the 7 R5 hits in Aug–Oct (script 07) and no others |
| Q8/Q16c | Only closed cases with `close_epoch ≤ as_of`; opened-but-open cases (`open ≤ as_of < close`) invisible |
| Q9 | Seeded from card C13487-K1 at HHG-014 `as_of`: returns the ring device and ≥ 43 other customers; from a random non-ring card: empty or hub-skipped |
| Q10 | `--as-of 2016-11-12 00:46:24` and `2016-11-22 20:11:00` each return exactly 2 devices, ring first (script 05); on data < 2016-11-01 the ring is flagged 2016-08-16 with 3 customers |
| Q11 | No chunk with `valid_from_epoch > as_of`; a `linked` label requires a shared eligible entity |
| Q12 | Σ\|amount\| equals pandas; rejects ids after `as_of` |
| Q13 | Returns 33 hits on Aug–Oct data (script 07); at HHG-006's `as_of` the card's four purchases are visible |
| Q16a/b | Idempotent (repeat write keeps one vertex, `revision` increments); unknown IDs rejected |
| Q17/Q18 | Detect injected future ID / missing ID |

**F. MCP wrapper**
1. Model cannot supply `as_of` (parameter absent; injection attempt in arguments is ignored and logged).
2. Every result checked; a forced violation is dropped and counted.
3. Blocked fields (§12) never appear in any tool output (schema test over all 20 cases × all tools).
4. Parameter caps enforced.

**G. GraphRAG**
1. Retrieval for a case at `as_of` never returns a chunk with `valid_from_epoch > as_of` (test at 5 dates).
2. Policy chunks retrievable by rule id R1–R10 and action ids exactly.
3. Retrieval results identical when `risk_score` values are perturbed.

**H. Case write / memory**
1. `write_case` creates `FI_Case` + edges; `find_cases(card)` at a later `as_of` returns it, at an earlier `as_of` does not.
2. Re-run overwrites the same `graph_case_id` (revision++).
3. `written_to_graph=true` ⇒ the vertex exists and all referenced IDs exist (Q18).

**I. Policy engine (deterministic, no LLM)**
Table-driven tests, one per rule R1–R10 and per route: BLOCK_CARD route flips at exposure $2,500 (L1 ≤ 2,500 < L2); FILE_REPORT always L2; BLOCK_ALL_CARDS only under R10 conditions; R1 forbids BLOCK before verification when P < 0.70 on a single source; SAR conditions (§8.3); `sar.file ⇔ FILE_REPORT`; case creation at P ≥ 0.30.

**J. Answer-file validator**
Schema + §8.6 constraints on all 20 outputs; failing files produce zero-tolerance errors. Includes IDs existing, exposure equality, date format, enum values.

**K. End-to-end discovery**
1. With `risk_score` masked to a constant in all tool outputs, the pipeline still flags the ring device at HHG-014's `as_of` and lists ≥ 43 connected cards.
2. On data < 2016-11-01 the ring is flagged before its first label is visible (script 05: 14.9 days).
3. HHG-006: scan_bursts/burst_check surface the four-purchase, ~$1,906 burst without using the seed marker.

**L. Leakage red-team**
For each of the 20 cases run the whole pipeline twice: (a) normal; (b) with every row after `as_of` randomised/deleted. Outputs (evidence, affected txns, exposure, actions) MUST be identical.

---

## 14. Build order

1. Environment verification (TigerGraph version, vector support, MCP tools) — §17-B1/B2.
2. Ingestion + §13-A/B.
3. Temporal core: Q1, Q5, Q17 + §13-C.
4. Hub gating: Q4, Q6, Q9, Q10 + §13-D.
5. Remaining queries + §13-E.
6. Wrapper + §13-F.
7. GraphRAG + §13-G.
8. Case memory + §13-H.
9. Policy engine + §13-I, validator §13-J.
10. Agent, then §13-K/L.
11. ADVANCED items.

---

## 15. Repository layout (created in Phase 6)

```
README.md  DATASET_SPEC.md  .gitignore
docs/        specs and findings (tracked)
scripts/     analysis/ oracles + README.md (tracked)          data/staged/  generated, git-ignored
graph/       schema/ loading/ queries/ vector/ tests/  (TigerGraph objects; empty until the build phase)
src/         ingestion/ (staging; imports scripts/analysis/common.py), fraud_tools/ (MCP wrapper), policy/ (rule engine),
             agent/, eval/ (runner, validators)
tests/       unit + acceptance tests that call the scripts/analysis oracles
config/      secret-free templates only; real credentials git-ignored
cases/       the 20 answer JSONs (added by the evaluation runner)
```

---

## 16. Differences from `docs/graph_architecture.md` (this spec wins)

| Topic | Phase 3 | This spec (validated) |
|---|---|---|
| Device sharing eligibility | ≤ 20 LOW, 21–100 MID allowed with compound test, `n_known ≥ 3` gate, GENERIC class | degree ≤ 5 strong / 6–20 moderate / > 20 none; only the all-null profile is blocked by content; no `n_known` gate |
| Ring rule | included `n_known ≥ 3`, proxy/new thresholds | `customers ≥ 3, new ≥ 0.9, proxy ≥ 0.5`, 30-day warm-up suppression |
| Device props | `hub_class`, `n_customers_snap` | `snap_class`, `snap_customers` (pre-filter only), no `quality` field |
| `SEEN_ON` | `first_epoch` | unchanged (+ explicit: no last_seen/counter) |
| Region/email evidence | Tier 3 (LOW/MID) | attribute only unless deg ≤ 20 and ≥ 2 independent cases |
| Evidence hierarchy | identity anomalies tier 5 | identity anomalies LOW, with stacking guard |
| Removed signals | — | V93/V52/V79, region "never seen", `id_15 = New` as evidence |
| `Transaction.v*` list | 9 named V | 9 named V (`v51,v52,v79,v93,v94,v217,v258,v264,v308`) stored as context only |
| Query names | Q0–Q18 | same numbering; Q5 now returns `eligible_sharing`, `ring_suspect`, `unlabeled_customers` |
| DDL form | global `CREATE VERTEX/EDGE`, `CREATE GRAPH (*)`, `embedding LIST<FLOAT>`, `WITH primary_id_as_attribute` | local-schema `ADD VERTEX/EDGE` jobs, `ADD VECTOR ATTRIBUTE`, INT/DATETIME vertex indexes, no `(*)` (E1–E5) |
| Query language | unspecified | v1 syntax, no `SYNTAX v3`, `candidate_set` for retrieval, two-step hub guard (E5–E7) |
| Credential | `TG_API_TOKEN`/`TG_SECRET` ambiguity | `TG_SECRET` only; secret rotated 2026-09-19 |
| Names | `Case`, `proxy` | `FI_Case`, `proxy_type` (reserved words, E12) |
| Vertex declaration | plain `ADD VERTEX` | `WITH primary_id_as_attribute="true"` on every vertex (E14) |
| Load plan | chunks of 25–50k rows [unmeasured] | chunks ≤ 200,000 rows (E15) |
| Hub guard cost | unknown | ≈ 1 s per call incl. gateway latency (E16) |

---

## 17. Remaining implementation blockers

**Blocking (must be resolved before or at the start of the build)**

| # | Blocker | Needed |
|---|---|---|
| ~~B1~~ | **RESOLVED 2026-09-19 (see below).** Formerly: **TigerGraph environment not provisioned or verified.** Version, whether native vector attributes exist, GSQL secondary-index and accumulator early-exit syntax, and loading method (upload vs S3) are unknown | Create workspace, record version, run a 3-line probe for each **[verify]** item |
| B2 | **`tigergraph-mcp` documented (1.0.3, 69 tools) but not installed/exercised**; the wrapper design assumes read-query, upsert and vector-search endpoints | Install and list tools; adapt wrapper table §11 |
| B3 | **Probability calibration is undefined.** Signals have rated evidence value but no validated mapping to `fraud_probability`; the only labels are Jul–Oct, unlabeled ≠ legitimate, and the score is circular | Decide a transparent scoring/calibration scheme (e.g. per-tier priors from §6, tested on Jul–Oct only) before the policy engine; do not fit to the 20 cases |
| B4 | **Customer/step-up response simulator undefined** (§8.4): deterministic rule and seed must be chosen without looking at benchmark labels (none exist) | Specify and freeze before the agent runs |
| B5 | **No LLM / embedding model / API budget decision** (token limits; `tokens` is a scored field) | Choose models; define per-case token budget |
| B6 | **R7 cannot be evaluated**: the data has no merchant; "same merchant, amount, monthly" must be approximated by amount/ProductCD/recipient-email/day-of-month regularity, which is unvalidated | Define an explicit proxy or state R7 as not evaluable |
| ~~B7~~ | **RESOLVED (Phase 6: dedicated repo, `.gitignore`).** Formerly: **No project repository**: this folder sits inside a personal home-directory git repo (unrelated untracked files); 708 MB CSVs must not be committed | `git init` a dedicated repo (or move), add `.gitignore` for data, cache, outputs |

**Resolved**
- B1: TigerGraph 4.2.5 Enterprise on Savanna verified live; local schema, indexes (job accepted), vector attribute + `vectorSearch` with `candidate_set`, loading via `/restpp/ddl`, accumulators and the two-step hub guard all work (§2.0).
- B7: repository initialised and ignored data/secrets (Phase 6).

**Phase 8 step 0 outcome**
- N1 RESOLVED: collisions are harmless; `Case` and `proxy` are reserved → `FI_Case`, `proxy_type` (E12, E13).
- N2 PARTLY RESOLVED: index existence confirmed via `LS`/schema endpoint; **speed benefit unresolved** (REST latency masks it) — measure server-side in the real graph (E3).
- N3 RESOLVED for chunked loads: 200,000 full-width rows / 34.6 MB in 8.6 s; 600,000 narrow rows accepted; a single ~100 MB body untested (E15).
- N4 OPEN: a four-query `INSTALL QUERY` on an 800,000-vertex graph did not complete before the probe was aborted; install queries **one per request** and record install times on the real graph (E6).
- N5 OPEN: the installed-query exact count (`S.size()`) was not verified; rely on the polled `stat_*` counts plus per-load `validLine` totals (E9).
- Latency: ~0.8 s gateway latency per REST call is a design input for the tool budget (E16).

**Non-blocking, known limitations (carry into the build)**
- One historical ring (n = 1): S01 is a discovery aid; thresholds are tuned by alert budget.
- Burst family (S07) fitted to 5 cases; 18 of its 33 Aug–Oct hits are unlabeled.
- 42 of 52 ring transactions have no label; no answer key exists, so evaluation is by consistency and leakage tests, not accuracy.
- Provenance of unnamed C/D/V features is unknown (may embed future information) — LOW only.
- Regulatory PDFs are links, not files; ingestion needs download approval (ADVANCED).
- Card-testing labels in history are broader than policy R5 (1/16 match) — apply R5 as written.
- Null-`card6` cards (672, K1) are modelled as separate cards per the data; semantics unresolved.
- `snap_*` uses Nov-1 snapshot; later monitoring beyond the benchmark period needs a versioned re-snapshot.
