# scripts/

Reproducible analysis code for Phases 2–4. It rebuilds every number quoted in `docs/signal_validation.md` and the
reference oracles that the build phase (TigerGraph queries, MCP wrapper, agent) must be tested against.
No TigerGraph, no LLM, no network access is needed.

## Setup

```bash
python -m pip install -r scripts/requirements.txt      # pandas, numpy, scipy, pyarrow
```

Run everything from the dataset folder (`HHGOA_IEEE/`, the folder that holds `transactions.csv`). Scripts locate the CSVs
relative to their own path; set `HHG_DATA_DIR=/path/to/csvs` if the CSVs live elsewhere.

First run builds a compact cache `scripts/analysis/.cache/master.parquet` (built during the first script, ≈ 15 s, one row per transaction,
identity/device/card_id/case-label joined). Delete `.cache/` to force a rebuild. Results and PASS/FAIL detail are written
to `scripts/analysis/output/` (git-ignored).

## Run all

```bash
python scripts/analysis/run_all.py            # everything, ~1–2 min including the first cache build
python scripts/analysis/run_all.py --fast     # skips the slow region/email link tables in 07
```
Exit code = number of failing scripts. Every script prints `[PASS]/[FAIL]` lines and ends with `N/N checks passed`;
a script also exits non-zero if any check fails.

## Library modules (imported, not run)

| File | Purpose |
|---|---|
| `common.py` | Paths, constants (`T0`, `RING_PROFILE`, `EVAL_START_EPOCH`, `HIST_END_EPOCH`), CSV loading, `derive_card_ids`, `build_master`, `Checks` |
| `asof.py` | As-of primitives: `device_pit` (incremental), `dev_stats_oracle` / `view_txns` / `view_cases` / `label_visible` / `assert_no_future` (oracle), `ring_flag` |
| `signals.py` | Card-level point-in-time features (`card_pit`), `summarize`, `auc`, `link_signal` |

Conventions used everywhere: `epoch` = `TransactionDT` (seconds since 2016-07-02 00:00:00); `as_of` = a case's `opened_at`
converted to the same units; label visibility uses `close_ep` (closed_at), never `open_ep`.

## Scripts

| # | Script | What it reproduces | Docs section | Arguments |
|---|---|---|---|---|
| 01 | `01_card_id.py` | Card ID reconstruction: `customer_id + "-K" + rank(card6, NaN first)`; 14,955/14,955 closed-case txns, 5,565 cases, 20/20 benchmark cards; shows first-seen/frequency alternatives fail (< 10%) | Findings §1 | none |
| 02 | `02_device_degree_asof.py` | Device degree / state as of a timestamp: incremental table vs brute-force oracle at 400 random points; ring degree 44 as-of HHG-014 vs 52 with all data | Signal validation §6-T1 | `--device "<profile or D_id>" --as-of "<timestamp>"` prints one device's state; `--samples N` |
| 03 | `03_temporal_filter.py` | Temporal filtering: future-corruption invariance (60 trials, 0 violations) and a control that catches a deliberately leaky implementation | §6-T2 | `--trials N` |
| 04 | `04_closed_case_visibility.py` | Closed-case visibility: `closed_at` vs `opened_at` gating (918 vs 1,180 flagged; 31.5% vs 35.5% precision), cases in flight (mean 136.5), end-of-window degree leak | §6-T3, T4 | none |
| 05 | `05_ring_detection.py` | Ring rule (customers ≥ 3, new_share ≥ 0.9, proxy_share ≥ 0.5) on data < Nov 1: first flag 2016-08-16 with 3 customers, 52/54 txns flagged, 14.9-day lead over labels, 48/48 threshold grid, ablation | §1 | `--max-date`, `--min-cust`, `--new-share`, `--proxy-share`; `--as-of "<ts>"` lists devices flagged at that instant using data ≤ as_of only |
| 06 | `06_benchmark_leakage.py` | Benchmark leakage audit: for each of the 20 cases, the as-of customer view (`epoch ≤ opened_at`), visible closed cases, hidden future rows, device state as-of vs full data. Writes `output/06_benchmark_leakage.csv`. Produces **no verdicts** | §6-T6, T7 | none |
| 07 | `07_signal_validation.py` | Signal tables: device sharing by as-of degree, card-level PIT signals with channel-specific base rates, region/email link tables, within-channel AUC stability, snapshot drift, R5 coverage | §2, §3, §4, §0.1 | `--skip-links` |

Examples:

```bash
# state of the suspicious device just before HHG-014 opens (data <= as_of only)
python scripts/analysis/02_device_degree_asof.py --device "SM-G935F Build/NRD90M | Android 7.0 | chrome 62.0 for android | 1920x1080" --as-of "2016-11-22 20:11:00"

# which devices would the ring scan flag at that moment?
python scripts/analysis/05_ring_detection.py --as-of "2016-11-22 20:11:00"

# try a stricter rule on July-October only
python scripts/analysis/05_ring_detection.py --proxy-share 0.7
```

## Outputs (`scripts/analysis/output/`)

`NN_name.json` holds the metrics, `NN_name_checks.json` the PASS/FAIL list, `06_benchmark_leakage.csv` the per-case audit.
These files describe the benchmark cases' data and **must not be given to the agent** (see `docs/implementation_spec.md` §12).

## Expected reference values (regression targets for the build phase)

| Quantity | Value |
|---|---|
| Derived cards / customers | 14,317 / 13,553 |
| Card-ID reproduction | 14,955/14,955 txns, 5,565/5,565 cases, 20/20 benchmark |
| Ring (data < Nov 1) | 54 txns, 24 customers; first flag 2016-08-16 14:11; 3 customers at flag; 52 flagged on arrival; lead 14.9 d |
| Ring degree as-of | 24 at 2016-11-12 00:46:24; 44 at 2016-11-22 20:11:00; 52 with all data |
| Devices ever flagged, proposed rule | 6 (ring + 5 July warm-up) ; ablation 439 / 82 / 6 |
| S02 (deg ≤ 5 / 6–20, `nk ≥ 3`) | 175 flagged, 87 fraud / 743 flagged, 202 fraud |
| Label-gating leak (deg ≤ 20, `nk ≥ 3`) | closed_at 918 flagged / opened_at 1,180 |
| Benchmark device customers as-of vs full | HHG-013 59 vs 253; HHG-014 44 vs 52; HHG-017 164 vs 299; HHG-009 1,001 vs 1,011 (null profile) |

## Notes and limits

- Metrics use Aug 1 – Oct 31; July is warm-up. Nothing dated ≥ 2016-11-01 enters scripts 05 (default) and 07.
- "Unlabeled" means no closed case, not "legitimate"; precision figures are lower bounds.
- Scripts 02, 03, 06 use the full file only to *test* as-of behaviour (what would leak), never to produce evidence.
- Seeds are fixed (`random_state` 3, 7, 11).
