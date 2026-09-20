# TigerGraph environment — verification record (Phase 7)

Date: 2026-09-19. Scope: verify workspace facts, GSQL syntax, and the official MCP **before** any schema or data work.
Nothing was created, modified, or uploaded in TigerGraph. The existing "Transaction Fraud" schema was not touched (it was not even visible — see §1).

Legend: **VERIFIED-DOC** = confirmed against official TigerGraph documentation fetched today (links in §8). **UNVERIFIED-LIVE** = cannot be confirmed without a live connection to the workspace. **ACTION** = something the user must provide.

---

## 1. Workspace status: NOT verifiable yet

| Item | Status | Detail |
|---|---|---|
| Workspace config on this machine | **Not found** | No `.env`/config for TigerGraph in the repo (`config/` holds only the example template); no `TG_*` environment variables; no TigerGraph MCP server configured (`claude mcp list` shows only Claude Docs); `~/.env` contains an unrelated `GOOGLE_API_KEY` (name only inspected, value not read); no `pyTigerGraph`/`tigergraph-mcp` installed |
| Savanna console | **Not logged in** | The Chrome instance connected to this session shows the Savanna login page (`auth.tgcloud.io`). Logging in (password, Google sign-in) is user-only, so I stopped there. The tab I opened was closed. Nothing else was read |
| Workspace host URL | UNVERIFIED-LIVE | Needed as `TG_HOST` (form `https://<workspace>.i.tgcloud.io`) |
| Database version | UNVERIFIED-LIVE | Savanna supports DB ≥ 4.0.0 (VERIFIED-DOC), but the workspace's actual version is unknown. Docs today list **4.2.x as the latest GA (4.2.5, released 2026-09-02)** and 4.3.0-rc1 as preview |
| Vector (TigerVector) support | UNVERIFIED-LIVE | Documented for 4.2+; the Savanna FAQ mentions "Hybrid Graph+Vector Search" without specifics. Must be probed (§6) |
| Existing graph "Transaction Fraud" | UNVERIFIED-LIVE | Not inspected; **must not be modified**. See §5 for the isolation strategy (local schema) |
| Edition limits (Community vs Enterprise) | UNVERIFIED-LIVE | Vector dimension limit is 4,096 (Community) vs 32,768 (Enterprise) (VERIFIED-DOC) — irrelevant to our embedding size but relevant to feature checks |

**ACTION (user):** provide connection settings **through `.env` in the repo root (git-ignored), not in chat**:
`TG_HOST`, and either `TG_API_TOKEN` (preferred) or `TG_USERNAME`/`TG_PASSWORD`, plus `TG_GRAPHNAME` for our new graph. To get a token: Savanna workspace → Admin Portal → User Management → create a secret → request a token from it (§4). Also tell me the **exact name of the workspace's existing graph** so the probe can refuse to write to it.

---

## 2. Version and capability matrix (from official docs)

| Capability | Requires | Status |
|---|---|---|
| GSQL vertex/edge DDL, loading jobs, accumulators, datetime functions | any 4.x | VERIFIED-DOC (4.2 reference) |
| Secondary index on vertex attributes | 4.x (`ADD INDEX`) | VERIFIED-DOC (search result of the schema-change reference); syntax page did not show an example — **probe live** |
| Vector attributes + `vectorSearch` | **4.2+** | VERIFIED-DOC; live support UNVERIFIED |
| Savanna data sources: S3, GCS, Azure Blob, local files, Snowflake, JDBC | Savanna | VERIFIED-DOC (FAQ) |
| Multiple graphs per workspace, per-graph schema | Savanna | VERIFIED-DOC (FAQ, "MultiGraph") |
| Paused workspace resumes on incoming requests; compute billing stops while paused, storage billing continues | Savanna | VERIFIED-DOC |
| Free tier | — | "Free credits, no free instances"; storage/memory limits **not documented** → check credit balance before loading 590k txns / ~2.5M edges |

---

## 3. Official TigerGraph MCP (VERIFIED-DOC, `github.com/tigergraph/tigergraph-mcp` + PyPI JSON)

| Item | Value |
|---|---|
| Package | `tigergraph-mcp` — **1.0.3, released 2026-09-16**; requires Python ≥ 3.10 (local: 3.10.9 ✔) |
| Dependencies | `pyTigerGraph>=2.0.4`, `mcp>=1.0.0`, `pydantic>=2.0.0`, `click`, `python-dotenv>=1.0.0`; optional `[llm]` (langchain) |
| Install | `pip install tigergraph-mcp` (or `conda install -c tigergraph tigergraph-mcp`); HTTP mode also needs `uvicorn starlette`; **we do not need `[llm]`** |
| Supported DB | minimum 4.1; 4.2+ recommended (TigerVector, hybrid retrieval) |
| Transports | stdio (default) `tigergraph-mcp [--env-file .env] [-vv]`; HTTP `--transport streamable-http --host … --port …` |
| Tool filtering | `--allowed-tools read-only` (37 tools), `--blocked-tools destructive`; per-session over HTTP with header `X-TG-Tools` |
| Profiles | prefixed env vars (`PROD_TG_HOST`, …), per-call `profile=` |

### 3.1 Environment variables (exact names)

| Variable | Default | Note |
|---|---|---|
| `TG_HOST` | `http://127.0.0.1` | for Savanna: `https://<workspace>.i.tgcloud.io` |
| `TG_TGCLOUD` | — | set `true` for cloud mode |
| `TG_API_TOKEN` / `TG_JWT_TOKEN` | — | Bearer token; overrides username/password |
| `TG_USERNAME`, `TG_PASSWORD` | `tigergraph` | fallback auth |
| `TG_GRAPHNAME` | — | **note: not `TG_GRAPH`** |
| `TG_RESTPP_PORT`, `TG_GS_PORT` | 9000, 14240 | defaults work for cloud |
| `TG_SSL_PORT` | — | `443` for cloud |
| `TG_CERT_PATH` | — | optional |

`config/tigergraph.example.env` has been aligned to these names (previously `TG_GRAPH`, `TG_SECRET`).

### 3.2 Tool names (69 tools reported; names exact)

- Discovery/connection: `tigergraph__discover_tools`, `tigergraph__get_workflow`, `tigergraph__get_tool_info`, `tigergraph__list_connections`, `tigergraph__show_connection`, `tigergraph__authenticate`
- Schema/graphs: `tigergraph__get_global_schema`, `tigergraph__get_graph_schema`, `tigergraph__show_graph_details`, `tigergraph__list_graphs`, `tigergraph__create_graph`, **`tigergraph__drop_graph`**, **`tigergraph__clear_graph_data`**
- Nodes/edges: `add_node(s)`, `get_node(s)`, **`delete_node(s)`**, `has_node`, `get_node_edges`, `add_edge(s)`, `get_edge(s)`, **`delete_edge(s)`**, `has_edge`
- Queries: `run_query` (interpreted), `run_installed_query`, `install_query`, **`drop_query`**, `show_query`, `get_query_metadata`, `is_query_installed`, `update_query_description`, `get_query_description`, `get_neighbors`
- Loading: `create_loading_job`, `run_loading_job_with_file`, `run_loading_job_with_data`, `get_loading_jobs`, `get_loading_job_status`, **`drop_loading_job`**
- Stats: `get_vertex_count`, `get_edge_count`, `get_node_degree`
- GSQL: **`tigergraph__gsql`** (arbitrary GSQL), `generate_gsql`, `generate_cypher` (LLM extra)
- Vector schema: `add_vector_attribute`, `drop_vector_attribute`, `list_vector_attributes`, `get_vector_index_status`
- Vector data: `upsert_vectors`, `load_vectors_from_csv`, `load_vectors_from_json`, `search_top_k_similarity`, `fetch_vector`
- Data sources: `create/update/get/drop_data_source`, `get_all_data_sources`, `drop_all_data_sources`, `get_data_source_types`, `preview_sample_data`

(all prefixed `tigergraph__`; bold = destructive)

### 3.3 Consequences for our design (MCP is a build/dev interface, not the agent interface)
1. The MCP exposes `tigergraph__gsql`, `run_query`, `drop_graph`, `clear_graph_data`, raw node/edge readers, and `search_top_k_similarity` — all of which bypass the `as_of` contract. Per `docs/implementation_spec.md` §12 the agent MUST NOT get these. The agent-facing "fraud-tools" wrapper (spec §11) calls only *installed* queries; the raw MCP is for the developer/build phase.
2. **Use `--blocked-tools destructive` for any MCP instance the agent process can reach**, and run a **read-only** instance (`--allowed-tools read-only` or a wrapper-level allow-list of `run_installed_query`, `get_vertex_count`, …) for the wrapper. A separate full-privilege instance is used only by the build scripts, never by the LLM.
3. The wrapper must not depend on `tigergraph__search_top_k_similarity` for temporal filtering (its signature has no documented `as_of`/candidate-set hook); do vector search in an **installed GSQL query** with `vectorSearch` + `candidate_set` (§5.6).
4. Tool names in `implementation_spec.md` §11 (agent-facing names) are ours; the underlying TigerGraph tool for each is `tigergraph__run_installed_query` — no change needed.

---

## 4. Connection requirements (Savanna)

| Requirement | Detail (VERIFIED-DOC via search result; confirm live) |
|---|---|
| Transport | HTTPS on port **443** |
| REST endpoints | `https://<host>/restpp/<endpoint>` |
| Auth | create a **secret** in the Admin Portal (User Management) → request a token: `POST /gsql/v1/tokens` with the secret in the body → returns a Bearer token with an expiry date |
| MCP | `TG_HOST`, `TG_TGCLOUD=true`, `TG_API_TOKEN`, `TG_SSL_PORT=443` (§3.1) |
| Roles | the token's user needs privileges to create a graph, run schema-change and loading jobs, and install queries. Prefer a user scoped to **our** graph, not the one that owns "Transaction Fraud" |
| Token expiry | unspecified in the pages fetched; the build scripts must re-request tokens |
| Workspace pause | a paused workspace resumes on requests; the first call may be slow |

The Savanna "Connect From API" panel in the console generates working snippets for the workspace (JavaScript, cURL, Python) and is the authoritative source for the exact host/paths — **read it once logged in**.

---

## 5. GSQL syntax constraints (VERIFIED-DOC, 4.2 reference) — and corrections to the spec

### 5.1 Vertex / edge definitions
- `CREATE VERTEX V (PRIMARY_ID id STRING, attr TYPE [DEFAULT v], …) [WITH primary_id_as_attribute="true"]`; or `PRIMARY KEY` form. Types: `STRING, INT, UINT, DOUBLE, FLOAT, BOOL, DATETIME`, containers (`LIST`, …).
- `CREATE DIRECTED EDGE E (FROM A, TO B, attr TYPE …) WITH REVERSE_EDGE="R"`.
- **Multiple endpoint pairs** use `|` between complete pairs: `(FROM Case, TO ClosedCase | FROM Case, TO Case, …)`.
  **Correction:** `docs/implementation_spec.md` §2.1 wrote `TO ClosedCase | Case` — invalid. Fix before use.
- `CREATE GRAPH g (*)` includes all **global** types; `CREATE GRAPH g ()` creates a graph with a **local** schema.
- **Isolation from "Transaction Fraud":** global types are shared across graphs; a global `Transaction` vertex may already exist (it is in that graph's schema). Our spec uses type names `Transaction`, `Customer`, `Card`… **Decision:** create the graph with a **local schema** — `CREATE GRAPH <ourname> ()` then `CREATE SCHEMA_CHANGE JOB … FOR GRAPH <ourname> { ADD VERTEX …; ADD DIRECTED EDGE …; }` — because vertex/edge types with the same name are allowed when defined in different scopes. This leaves all global types and the existing graph untouched. The spec's `CREATE VERTEX` DDL must be rewritten as `ADD VERTEX` inside a local schema-change job, and `CREATE GRAPH FraudGraph (*)` must **not** be used (it would pull in every global type).
- Graph name: spec says `FraudGraph`; keep it, unless it collides with an existing graph (**ACTION:** confirm).

### 5.2 Loading jobs
```gsql
CREATE LOADING JOB job FOR GRAPH g {
  DEFINE FILENAME f;
  LOAD f TO VERTEX V VALUES ($0, $1, …) USING SEPARATOR=",", HEADER="true", QUOTE="double", EOL="\n";
  LOAD f TO EDGE E VALUES ($0, $1, $2 …);
}
RUN LOADING JOB job USING f="/path/file.csv"
```
- Columns by position (`$0`) or name (`$"col"`, with `HEADER="true"`). Options: `SEPARATOR`, `EOL`, `QUOTE`, `HEADER`, `NEW_VERTEX_ONLY`, `VERTEX_MUST_EXIST`, `JSON_FILE`.
- Conversion functions: `to_int`, `to_float`, `gsql_to_int/uint/bool`, `gsql_ts_to_epoch_seconds`, `to_datetime("YYYY-MM-DD HH:MM:SS")` (accepted formats `%Y-%m-%d %H:%M:%S`, `%Y/%m/%d %H:%M:%S`, `…T…000z`, epoch string).
- `DEFINE FILENAME f = "path"` for local server paths; on Savanna the source is a data source (S3/GCS/Azure/local upload/…), not a server path — the MCP's `run_loading_job_with_file` / `_with_data` are the practical local paths. **UNVERIFIED-LIVE:** upload size limits (none documented); test with a 1,000-row file.
- Empty CSV cells load as empty strings/defaults; our staged files must write `-1` for missing `addr1/addr2` (spec DEFAULT −1) and empty for optional strings.
- Loading edges auto-creates missing endpoint vertices unless `VERTEX_MUST_EXIST="true"` — set it on all edge loads to catch key mistakes.

### 5.3 Accumulators
13 types: scalar (`SumAccum`, `MinAccum`, `MaxAccum`, `AvgAccum`, `AndAccum`, `OrAccum`, bitwise, `DeviationAccum`, `DeviationPAccum`) and collection (`ListAccum`, `SetAccum`, `BagAccum`, `MapAccum`, `HeapAccum`, `GroupByAccum`, `ArrayAccum`).
- Global `@@name`, vertex-attached `@name`, edge-attached `EDGE @name`.
- `ACCUM` runs per edge/vertex during traversal (threads with mutual exclusion); `POST-ACCUM` runs after and only on vertex accumulators. Vertex/edge accumulators cannot be declared inside `FOREACH`/`WHILE`; block-scoped.
- **Consequence:** `SetAccum<STRING> @@customers` for as-of degree is legal but is one global structure updated by all threads. For high-degree devices we cap with an early-exit guard; **`BREAK` inside ACCUM is not available**, so the hub cut-off must be a separate pre-step (count distinct customers over `FROM_DEVICE` edges with `epoch <= as_of` using a `SumAccum` or `SetAccum` plus a `WHERE` on `@@` size in a later SELECT) — validate performance on the null profile (3,648 txns / 1,001 customers) live. Spec §5.2's phrase "early cut-off" is an implementation goal, not verified syntax.

### 5.4 Secondary indexes
- `CREATE GLOBAL SCHEMA_CHANGE JOB j { ALTER VERTEX V ADD INDEX idx ON (attr); … }` (search-result quote of the reference; the fetched 4.2 page did not show it — **probe live**, and check whether local jobs use `CREATE SCHEMA_CHANGE JOB j FOR GRAPH g { ALTER VERTEX V ADD INDEX … }`).
- Only **vertex** attributes; **single** attribute per index; types **STRING, UINT, INT, DATETIME** only; **not used when the predicate uses a built-in function.**
- **Consequences:** edge attributes (`epoch` on `MADE`, `FROM_DEVICE`, …) cannot be indexed — the temporal filter on edges is evaluated while scanning the adjacency list. That is acceptable: per-card and per-device adjacency lists are small except the mega-cards (max 14,932 txns) and the null device (3,648). Vertex indexes worth adding: `Transaction.epoch` (INT), `ClosedCase.close_epoch` (INT), `Case.as_of_epoch` (INT), `TextChunk.valid_from_epoch` (INT), `DeviceProfile.profile_str` (STRING). Do not write predicates as `datetime_to_epoch(t.ts) <= x`; compare the stored INT `epoch` directly so the index applies.

### 5.5 Temporal filtering
- We store `epoch INT` (seconds since 2016-07-02) plus `ts DATETIME`; all as-of predicates compare INT with INT (`e.epoch <= as_of`). Avoids datetime timezone and function overhead.
- Available query functions if needed: `datetime_to_epoch(DATETIME)→INT`, `epoch_to_datetime(INT)→DATETIME`, `now()` (UTC), `datetime_add/sub/diff`, `year()…second()`. Comparing DATETIME with INT directly is **not documented** — do not rely on it.
- `now()` must never be used in an investigation query (it would reintroduce wall-clock time).

### 5.6 Vector attributes and search (TigerGraph ≥ 4.2)
- Declared via schema change, **not** as a `LIST<FLOAT>` attribute (the spec's `embedding LIST<FLOAT>` is wrong):
  `ALTER VERTEX TextChunk ADD VECTOR ATTRIBUTE embedding(DIMENSION=<n>, METRIC="COSINE");` (options: `INDEXTYPE="HNSW"` default, `DATATYPE="FLOAT"` default; `DIMENSION` 1–4096 Community / 32768 Enterprise).
- Load: `LOAD f TO VECTOR ATTRIBUTE embedding ON VERTEX TextChunk VALUES ($0, SPLIT($1, ";")) USING SEPARATOR=",";` — nested built-in token functions are **not** allowed in vector loads.
- Search: `res = vectorSearch({TextChunk.embedding}, @@queryVec, k, {candidate_set: cs, ef: 64, distance_map: @@dist});`
- **`candidate_set` restricts the search to a pre-built vertex set — this is how we apply the temporal filter** (`cs = SELECT t FROM TextChunk:t WHERE t.valid_from_epoch <= as_of`). Vector attributes cannot be used directly in `WHERE`.
- Not supported: nested `ListAccum` inside `vectorSearch()`; `+` on `ListAccum`.
- Data volume: 5,565 closed-case chunks + ~50 policy chunks → trivial for HNSW; the embedding model dimension is our choice (see blocker B5).
- Fallback if the workspace is < 4.2 or vector is disabled: external vector store keyed by `chunk_id` (spec §10).

---

## 6. Live verification probe (to run once `.env` exists) — read-only until step 5

Probes must use a **throw-away graph** (e.g. `probe_tmp`, local schema) that is dropped afterwards, and must not reference the existing "Transaction Fraud" graph.

1. `GET /gsql/v1/version` (or GraphStudio About) → record DB version; `tigergraph__list_graphs`, `tigergraph__get_global_schema` (**read-only**: record existing graph names and global types; confirm whether a global `Transaction`/`Customer`/`Card` type exists).
2. `tigergraph__show_connection` → confirms profile/host/auth.
3. Create `probe_tmp` with `CREATE GRAPH probe_tmp ()`; local `SCHEMA_CHANGE JOB` with one vertex + one edge + a vertex `INT` attribute → confirm local schema works and **does not alter** the global schema afterwards (diff of `get_global_schema`).
4. `ALTER VERTEX … ADD INDEX` (local job form) → record exact accepted syntax and any error.
5. Vector: `ADD VECTOR ATTRIBUTE` (dimension 3), load 3 rows, `vectorSearch` with `candidate_set` → record result.
6. Loading: 1,000-row CSV via `run_loading_job_with_file`/`_with_data` → record limits and speed.
7. Accumulator/BREAK/early-exit test on a small graph (§5.3).
8. Drop `probe_tmp`.
Record all results in this file under §7.

---

## 7. Live results (2026-09-19, `scripts/tigergraph/probe_env.py`)

**Host:** `TG_HOST` = the Admin-Portal origin (`https://tg-…​.i.tgcloud.io`, port 443). **Auth:** the 32-character "Database Key" is a **secret**: `POST /gsql/v1/tokens {"secret": …}` → Bearer token (HTTP 200). Stored as `TG_SECRET`. **Version:** `GSQL 4.2.5`, RESTPP `4.2.5`, edition **enterprise** (release_4.2.5_08-26-2026), so the Enterprise vector limit (32,768) applies.
**Protection:** `Transaction_Fraud` was only read (`SHOW GRAPH *`); its description and the global vertex-type list were byte-identical before and after (steps 9c/9d). All writes went to one throwaway graph `hhg_probe_tmp`, which was dropped (9a). Final graph list: `['Transaction_Fraud']`. `FraudInvestigation` was **not** created.

| Capability | Result | Verified syntax / note |
|---|---|---|
| Token from secret | ✔ | JSON body `{"secret": …}`; raw secret as Bearer → 401 |
| GSQL over REST | ✔ | `POST /gsql/v1/statements`, body = GSQL text, **`Content-Type: text/plain`** (form default → 415). Multi-line bodies with `USE GRAPH g` first work |
| Local-schema graph | ✔ | `CREATE GRAPH g ()`; `CREATE SCHEMA_CHANGE JOB j FOR GRAPH g { ADD VERTEX …; ADD DIRECTED EDGE …; }` then `RUN SCHEMA_CHANGE JOB j`. Global schema unchanged afterwards |
| Multi-pair edge | ✔ | `ADD DIRECTED EDGE PMulti (FROM PVert, TO PVert \| FROM PVert, TO PChunk, w INT);` |
| Secondary index (local job) | ✔ (job accepted) | `ALTER VERTEX V ADD INDEX idx ON (attr);` on INT and DATETIME attributes. **Caveats:** `SHOW VERTEX` does not list indexes, so presence is unconfirmed by inspection; on the first run the INT-index job hit a 600 s client timeout, on the second it completed → treat schema jobs as potentially slow, use ≥ 240 s timeouts, run them sequentially |
| Vector attribute | ✔ | `ALTER VERTEX PChunk ADD VECTOR ATTRIBUTE emb(DIMENSION=3, METRIC="COSINE");` |
| Vector load | ✔ | `LOAD fx TO VECTOR ATTRIBUTE emb ON VERTEX PChunk VALUES ($0, SPLIT($1, ";")) USING SEPARATOR=",", HEADER="true", EOL="
";` |
| Vertex/edge load | ✔ | loading job + `POST /restpp/ddl/<graph>?tag=<job>&filename=<var>&sep=%2C&eol=%0A` with the CSV as body (works for local upload); `VERTEX_MUST_EXIST="true"` accepted; **do not wrap columns in `to_datetime()` / `gsql_to_bool()` inside `LOAD`** (parse error) — a `YYYY-MM-DD HH:MM:SS` string loads into DATETIME and `true/false` into BOOL directly |
| Counts | ✔ | `POST /restpp/builtins/<graph>` body `{"function":"stat_vertex_number","type":"*"}` (GET is rejected); edge equivalent `stat_edge_number` |
| Query syntax | ✔ | default (v1) pattern `FROM Seed:s -(PEdge:e)-> PVert:t` works; **`SYNTAX v3` rejects it** — do not add `SYNTAX v3` |
| Query install | ✔ | `CREATE OR REPLACE QUERY …` then `INSTALL QUERY name` in one request; ≈ 30–40 s per query; success text `succeeded: 1 … failed: 0` |
| Run installed query | ✔ | `GET /restpp/query/<graph>/<query>?as_of=160&qv=1&qv=0&qv=0` (`LIST<FLOAT>` = repeated params) |
| As-of edge filter | ✔ | `WHERE e.epoch <= as_of` on an edge attribute: at `as_of=160` 1 edge (`beta`, max epoch 100); at `as_of=400` 3 edges |
| Accumulators | ✔ | `SetAccum`, `SumAccum`, `MapAccum`, `MaxAccum`, vertex `@deg`, `POST-ACCUM` all compile and run |
| `vectorSearch` + `candidate_set` | ✔ | `vectorSearch({PChunk.emb}, qv, 2, {candidate_set: Cs, distance_map: @@dist})`; `Cs = SELECT c FROM PChunk:c WHERE c.valid_from_epoch <= as_of`. At `as_of=160` results were c2, c1 (c3, valid_from 300, never returned); at `as_of=400` c3, c2. Distances are cosine distances (0 = identical) |
| Two-step hub guard | ✔ | pre-count (`ACCUM t.@in_deg += 1`), flag in `POST-ACCUM` (`CASE WHEN t.@in_deg > max_deg THEN t.@is_hub += TRUE END`), then `SELECT … WHERE @is_hub == FALSE`; ran correctly (`max_deg=1` flags the degree-2 vertex) |
| Drop throwaway graph | ✔ | plain `DROP GRAPH g` **fails when it has jobs/queries**; use `DROP GRAPH g CASCADE` (only for our own throwaway name) |
| Not probed | — | `INTERPRET QUERY`, S3/GCS data sources, upload-size limits above a few rows (batch large files), MCP process itself (`tigergraph-mcp` not installed; auth works over the same REST endpoints) |

**Disclosure:** the server's `SHOW USER` returns a *masked* secret (first 3 / last 3 characters) for each user. The first probe run echoed that line to the terminal, so 6 of the 32 characters of the `HHGOA` secret were visible in this session's tool output. The full secret and the minted tokens were never printed (redaction is enforced in the probe; the `SHOW USER` step now prints counts only). Rotating the secret is optional and cheap if you want zero exposure.

**Consequences for the spec (apply in Phase 8):** local-schema `SCHEMA_CHANGE JOB … ADD VERTEX/EDGE` (§9 items 1–3 confirmed); vector attribute via `ADD VECTOR ATTRIBUTE` and `vectorSearch(..., {candidate_set})` (item 5 confirmed); query syntax must be v1 (no `SYNTAX v3`); loads use `/restpp/ddl` with plain columns; schema jobs sequential with long timeouts; `DROP GRAPH … CASCADE` for cleanup of our own graph only.

---

### 7.2 Phase 8 step 0 — compatibility probe on throwaway graphs (2026-09-19)

Scripts: `scripts/tigergraph/probe_reserved_words.py`, `probe_phase8_step0.py` (67 checks, **65 OK**), `probe_phase8_step0b.py` (follow-up, **aborted** at query install; results below marked ◐). Graphs used: `hhg_p8_names`, `hhg_p8_tmp`, `hhg_p8_tmp2` — all dropped (`CASCADE`); final `SHOW GRAPH *` = `['Transaction_Fraud']`. `Transaction_Fraud` description and the global vertex/edge lists were byte-identical before/after (checked in the probes and again after cleanup). `FraudInvestigation` was not created. Synthetic rows only (no dataset upload). Logs: `scripts/tigergraph/.step0.log`, `.step0_results.json`, `.step0b.log`.

| Item | Result | Detail |
|---|---|---|
| **Reserved words** | **`Case` (vertex type) and `proxy` (attribute) are reserved**; every other name tested is fine | 75 attribute names + 39 type names scanned with parse-only jobs. Error: *"The specified Identifier 'Case' is a reserved keyword"*; `proxy` made both `Transaction` and `FROM_DEVICE` fail to parse. **Decision: `FI_Case`, `proxy_type`** |
| Name collisions with `Transaction_Fraud`'s global types | none harmful | local `Card` and `DESCRIBES` (both also global names) created; global schema unchanged |
| Full 9-vertex / 18-edge schema | ✔ | created one type per job; `FI_Case` and all `CASE_*`, `SIMILAR_CASE`, `DESCRIBES` edges accepted |
| Multi-pair + reverse edge | ✔ | `SIMILAR_CASE`, `DESCRIBES` (`SHOW EDGE` shows both pairs and `REVERSE_EDGE`) |
| Primary-key attribute | ✔ (with option) | `s.id` not readable without `WITH primary_id_as_attribute="true"` (type-check error TYP-158); readable with it; real `Customer.customer_id` readable. `ADD VERTEX … WITH primary_id_as_attribute="true"` is accepted |
| Index existence | ✔ | 5 index jobs accepted; names visible in `LS` and `GET /gsql/v1/schema/graphs/<graph>` (14,877-byte JSON), not in `SHOW VERTEX` |
| Index speed | **unresolved** | indexed equality 827 ms vs non-indexed 837 ms vs indexed range 840 ms over 360,000 vertices → REST latency (~0.8 s) masks any server-side difference; server-side (`timestamp()`) timing not obtained ◐ |
| Load (49-column Transaction) | ✔ | job accepted; 10k rows 7.6 s (first-call warm-up), 50k 4.0 s, 100k 5.0 s, **200k rows / 34.6 MB in one POST 8.6 s (≈ 23k rows/s)**; every `validLine` matched; 2,000 Cards 6.1 s; **360,000 MADE edges 5.3 s** |
| Larger bodies ◐ | ✔ | 200,000 and **600,000 narrow rows (10 / 31 MB)** accepted in 8.9 / 9.6 s; 800,000 vertices confirmed by `stat_vertex_number`. A full-width ~100 MB body was not tested |
| Count statistic lag | observed | `stat_vertex_number` read 260,386 right after loading 360,000 (no rows lost) and in ◐ read 200,000 → 800,000 within ~10 s |
| Hub pre-count | ✔ | two-step guard over `DEVICE_OF` edges, `VERTEX<DeviceProfile>` parameter: 80 edges 826 ms; 3,648 edges (1,001 customers) median 983 ms / max 1,658 ms; 20,000 edges 780 ms; 100,000 edges (2,000 customers) 1,033 ms; `is_hub` correct; as-of cut returned exactly 41 of 80 edges. Cost dominated by ~0.8 s fixed latency per REST call |
| Query features | ✔ | v1 syntax, `PRINT … AS alias` with boolean expression, `CASE WHEN` in `ACCUM`, reverse-edge attributes, vertex-typed parameters, `INTERPRET QUERY` |
| Query install ◐ | **slow/unresolved** | single-query installs 30–40 s; a four-query `INSTALL QUERY` on an 800,000-vertex graph did not finish and the probe was killed (cause unknown: install time grows with graph size or the multi-query form is slow) |
| Cleanup | ✔ | `DROP GRAPH … CASCADE` succeeded; one call reported *"Other operation is running, try to lock the catalog"* before the graph disappeared |

**Consequences (all applied to `docs/implementation_spec.md` §2.0/§2.1/§2.5/§3/§5.2/§13/§16/§17):** `FI_Case`, `proxy_type`, `primary_id_as_attribute="true"` on all vertices, confirmed multi-pair reverse edges, indexes created but not relied on, chunked loads ≤ 200,000 rows, hub pre-count kept as a two-step guard, ~0.8 s per-call latency as a tool-budget input, install queries one per request.

### 7.3 Phase 8 step 1 — production graph created (2026-09-19)

`scripts/tigergraph/create_graph.py` created **`FraudInvestigation`** (local schema) and ran the **32 non-vector jobs** sequentially from the frozen DDL: 9 vertex jobs, 18 edge jobs, 5 index jobs (≈ 39 s per job, 1,245 s total; no failures, no retries). Post-run verification: **41/41 checks OK** — graph list `['Transaction_Fraud', 'FraudInvestigation']`; `Transaction_Fraud` description and the global vertex/edge schema byte-identical; schema endpoint reports 9 vertex types and 18 edge types (each with its reverse edge configured; `Transaction` = primary key `txn_id` + 48 attributes = 49); all five indexes present; no vector attribute; no reserved names; **graph empty (0 vertices)**. Not done, by instruction: the vector attribute (`fi_vec`, waiting for the embedding dimension, blocker B5) and any data load. Result file: `scripts/tigergraph/.create_graph_result.json`; log `.create_graph.log`.

### 7.4 Phase 8 step 2 — data load (2026-09-19)

Pipeline (`src/ingestion/`): `stage.py` (raw CSV → 19 headerless staged files) → `validate_staged.py` → `subset.py` (6,015-transaction validation batch) → `load.py` (loading job `fi_load` + upload + read-back). Everything went into `FraudInvestigation` only; `Transaction_Fraud` and the global schema were fingerprinted before/after and stayed byte-identical.

**Source-column confirmation:** `M1`–`M9` (positions 45–53), `D4` (33), `dist2` (13) are original `transactions.csv` columns; `DeviceType`, `id_15`, `id_23`, `id_30`, `id_31`, `id_33`, `DeviceInfo` are in `identity.csv`. Missingness: `dist2` 93.63 %, `D4` 28.62 %, `M1–M3` 45.93 %, `M4` 47.67 %, `M5` 59.36 %, `M6` 28.70 %, `M7–M9` 58.65 %, `DeviceType` 2.37 % of identity rows. `M4` values `M0/M1/M2`, others `T/F`.

**Staged validation (pre-send):** row counts equal spec §2.4 exactly (all 19 files); primary keys unique; 27 referential-integrity checks with zero dangling ids; all typed columns parse; `ts == T0 + epoch`; 5,000 random transactions equal the raw source in every mapped column; `card_id` of 14,955 closed-case txns and 20/20 flagged txns match; no label columns on facts.

**Missing-value convention (new constraint E18):** TigerGraph has no null for DOUBLE/INT, so missing numerics are stored as a per-column **sentinel** (recorded in `data/staged/*/manifest.json`): `-1` for `dist1, dist2, d1, d2, d3, d5, d10, v51, v52, v79, v93, v94, v217, v258, v264, v308` (all have minimum ≥ 0); **`D4 = -123`, `D15 = -84`** (they have negative values); `addr1/addr2 = -1`; missing strings = `""`; `Card.card2/3/5 = -1`. Columns with no missing values (`amount`, `risk_score`, `c*`) never use their sentinel. **Every query must treat the sentinel as "missing", never as a value.**

**Validation batch (6,015 txns / 208 cards; ring profile, null-type cards, one closed case per pattern, the 23-card connected list):** 19 files loaded in 55.6 s, `validObject == rows` for every chunk (0 rejected), counts equal the manifest, 60 random transactions read back with **all 49 attributes identical** (incl. `M1–M9`, `D4`, `dist2`, `DeviceType`, sentinels), quoted notes containing commas round-trip exactly, `s.txn_id` / `s.customer_id` usable in `WHERE`, `MADE.epoch ≤ as_of` and ring-device distinct-customer counts equal the staged values, reverse-edge degrees equal. (First verify pass had 3 false failures caused by my verifier — string-sorted epochs and the `Using graph` output prefix — fixed and re-run without reloading; 16/16 OK.)

**Full load:** 590,742 transactions in 3 chunks (28.0 s), all 19 files in **97.8 s** total, `validObject == rows` everywhere. Verified counts (forward edges; reverse edges auto-created): Customer 13,553 · Card 14,317 · Transaction 590,742 · DeviceProfile 9,706 · EmailDomain 60 · BillingRegion 332 · ClosedCase 5,565 · OWNS 14,317 · MADE 590,742 · NEXT 576,425 · FROM_DEVICE 144,432 · SEEN_ON 68,013 · PURCHASER_EMAIL 496,262 · RECIPIENT_EMAIL 137,453 · BILLED_IN 525,003 · CLOSED_ON_CARD 5,565 · CLOSED_ON_CUSTOMER 5,565 · CLOSED_INVOLVES 14,955 · CLOSED_CONNECTED_TO 92. Read-back checks 16/16 OK, including **the ring profile having exactly 44 distinct customers as of 2016-11-22 20:11:00**, matching the Phase 4 oracle (`scripts/analysis/02_device_degree_asof.py`). `TextChunk` and `FI_Case` are empty; the vector attribute is not added.

## 8. Sources
- TigerGraph MCP: <https://github.com/tigergraph/tigergraph-mcp>, PyPI JSON `tigergraph-mcp` (v1.0.3)
- Vector: <https://www.tigergraph.com/docs/gsql-ref/4.2/vector/>, <https://docs.tigergraph.com/gsql-ref/4.2/vector/>
- Schema: <https://www.tigergraph.com/docs/gsql-ref/4.2/ddl-and-loading/defining-a-graph-schema>, <https://www.tigergraph.com/docs/gsql-ref/4.2/ddl-and-loading/modifying-a-graph-schema>
- Loading: <https://www.tigergraph.com/docs/gsql-ref/4.2/ddl-and-loading/creating-a-loading-job>
- Accumulators: <https://www.tigergraph.com/docs/gsql-ref/4.2/querying/accumulators>
- Datetime: <https://www.tigergraph.com/docs/gsql-ref/4.2/querying/func/datetime-functions>
- Secondary index (search result of the reference): <https://docs.tigergraph.com/gsql-ref/current/ddl-and-loading/modifying-a-graph-schema>
- Savanna FAQ: <https://www.tigergraph.com/docs/savanna/main/resources/faqs>; Savanna API connection: <https://docs.tigergraph.com/savanna/main/workgroup-workspace/workspaces/connect-via-api>, REST auth: <https://docs.tigergraph.com/savanna/main/rest-api/>
- Version selector: <https://www.tigergraph.com/docs/>

---

## 9. Spec changes required before Phase 8 (GSQL) — **all applied 2026-09-19**
| # | Change to `docs/implementation_spec.md` |
|---|---|
| 1 | §2.1: `embedding LIST<FLOAT>` → vector attribute via `ALTER VERTEX … ADD VECTOR ATTRIBUTE` |
| 2 | §2.1: multi-pair edge syntax (`FROM a, TO b \| FROM c, TO d`) |
| 3 | §2.1/§3: use a **local-schema** graph (`CREATE GRAPH g ()` + `SCHEMA_CHANGE JOB … ADD VERTEX/EDGE`); never `CREATE GRAPH … (*)`; never touch global types or the existing graph |
| 4 | §5.2: "early cut-off" hub guard needs a two-step pre-count (no `BREAK` in `ACCUM`) — verify live |
| 5 | §7: Q11 vector search uses `vectorSearch(..., {candidate_set: cs})` with the temporal candidate set |
| 6 | §11: MCP instances: a full-privilege one for build scripts only; the agent path is read-only/installed-queries-only |
| 7 | `config/tigergraph.example.env` variable names (done) |

**Status:** items 1–7 above and the Phase 8 step-0 consequences (§7.2) are reflected in `docs/implementation_spec.md`. Open: index speed (server-side timing) and multi-query install time.
