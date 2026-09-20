# Fraud Investigation Command Center (UI)

A read-only analyst workspace over the deterministic investigation agent. The backend stays authoritative: this app only presents what it produced
(`cases/HHG-###.json` answer files plus the matching stored agent records in `demo/records/`).

## Run

```
python src/ui_api/server.py            # API on http://127.0.0.1:8787 (also serves the built UI)
cd ui && npm install && npm run build  # once, to build the UI that the server serves
```

Open http://127.0.0.1:8787. For hot-reload development run `npm run dev` in `ui/` (http://localhost:5173, proxies `/api` to the backend).

Demo mode: Overview → **Launch demo** (HHG-014), or open `#/investigations/HHG-014?demo=1`.

## Boundaries

- The API is read-only: GET only, no GSQL, no `as_of` parameter, no credentials in any response. The one live call (`/graph-check`) reads a single `FI_Case`
  vertex through the existing whitelisted graph client and degrades gracefully when TigerGraph is unavailable. Everything else works offline.
- Customer and step-up responses are simulated by a deterministic responder and are labelled **Simulated** wherever they appear.
- `fraud_probability` is not stated (calibration gates did not pass); the UI says so and never shows a number.

## Tests

```
cd ui && npm test                       # rendering and integration paths
python -m unittest tests.test_ui_api    # API values equal the validated backend outputs
```

`demo/records/` is produced by `python src/ui_api/export_records.py <run_dir>` from a completed deterministic run and is checked against `cases/`.
