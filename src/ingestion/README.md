# src/ingestion

Raw CSV -> staged CSV -> TigerGraph `FraudInvestigation` (never `Transaction_Fraud`).

```bash
python src/ingestion/stage.py                                   # -> data/staged/full  (19 headerless CSVs + manifest.json with sentinels)
python src/ingestion/validate_staged.py --full                  # counts, PKs, referential integrity, types, raw fidelity (must pass first)
python src/ingestion/subset.py                                  # -> data/staged/validation (~6k txns, referentially closed)
python src/ingestion/load.py --stage data/staged/validation --phase validation   # needs an EMPTY graph
python src/ingestion/load.py --stage data/staged/full --phase full               # upserts everything
python src/ingestion/load.py --stage data/staged/full --verify-only              # read-back checks only
```
Secrets come from `.env` (`TG_HOST`, `TG_SECRET`, `TG_GRAPHNAME=FraudInvestigation`); the client refuses any other graph and any statement naming the protected graph.
Missing numeric values are sentinels (see `docs/implementation_spec.md` E18) - treat them as missing.
