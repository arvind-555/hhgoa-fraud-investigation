# graph/

TigerGraph implementation of the fraud-investigation graph. **Empty on purpose in Phase 6** — no GSQL, loader, or
query has been written yet. Everything below is specified in `docs/implementation_spec.md` (the source of truth; §-numbers
refer to it) and must be built in the order of its §14.

## What will live here

```
graph/
  schema/        schema.gsql                 vertex + edge DDL and CREATE GRAPH FraudGraph        (spec §2)
  loading/       loading jobs, one per staged file; load order documented                    (spec §3)
  queries/       one installed GSQL query per file, Q0-Q18                                   (spec §7)
  vector/        vector-attribute or external-store setup for TextChunk                      (spec §10)
  tests/         GSQL-vs-oracle comparison drivers (calls scripts/analysis/asof.py)          (spec §13)
```

| Area | Scope | Verified by |
|---|---|---|
| Schema | 9 CORE vertices (Customer, Card, Transaction, DeviceProfile, EmailDomain, BillingRegion, ClosedCase, Case, TextChunk), 22 edge types, no label/outcome attribute on facts | object counts in spec §2.4; acceptance A |
| Loading | staged CSVs produced by `ingestion/` (Python, reuses `scripts/analysis/common.py`) | counts, card-ID reproduction 14,955/14,955 and 20/20 |
| Queries | time-bounded traversals with a required `as_of`; hub gating by as-of degree; closed-case visibility on `close_epoch` | acceptance C, D, E: equal to `scripts/analysis/asof.py` oracles |
| Vector | `TextChunk.embedding` with `valid_from_epoch` pre-filter | acceptance G |

## Rules for anything added here
1. Every query takes `INT as_of` with no default and filters on **edge** `epoch` (spec §4).
2. No stored live aggregates and no labels on Transaction/Card/Customer/Device/Region/Email.
3. Labels exist only via ClosedCase and become visible at `close_epoch <= as_of`.
4. Hub entities are never expanded (spec §5); they are returned as `skipped_hubs`.
5. `risk_score` is never a filter or ranking key.
6. No credentials in this folder. Connection settings come from environment variables (see `config/`).

## Status
| Item | State |
|---|---|
| TigerGraph workspace | not created |
| Version / vector support / MCP tool names | unverified (spec §17 B1, B2) |
| GSQL written | none |
