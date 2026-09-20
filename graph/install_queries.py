"""Create (compile) and install the fi_* GSQL investigation queries on FraudInvestigation. One query per request (spec E6).
Only touches queries named fi_* in graph FraudInvestigation; never Transaction_Fraud.

Usage:
  python graph/install_queries.py --check [names...]     # CREATE OR REPLACE only (fast syntax/semantic check, saved as DRAFT if it fails)
  python graph/install_queries.py --install [names...]   # CREATE OR REPLACE + INSTALL QUERY, one at a time, timings printed
  python graph/install_queries.py --list                 # installed fi_* queries (SHOW QUERY *)
"""
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "ingestion"))
import tg  # noqa: E402  (guarded admin client: fixed graph, refuses the protected graph)

QDIR = ROOT / "graph" / "queries"
args = [a for a in sys.argv[1:] if not a.startswith("--")]
mode = "check" if "--check" in sys.argv else "install" if "--install" in sys.argv else "list" if "--list" in sys.argv else None
if mode is None:
    sys.exit(__doc__)


def files():
    fs = sorted(QDIR.glob("fi_*.gsql"))
    return [f for f in fs if not args or f.stem in args]


def create(f):
    body = f.read_text(encoding="utf-8")
    name = re.search(r"CREATE OR REPLACE QUERY (\w+)\(", body).group(1)
    assert name == f.stem and name.startswith("fi_"), f"query name must equal file name and start with fi_: {name}"
    t0 = time.perf_counter()
    s, e, m = tg.gsql(f"USE GRAPH {tg.GRAPH}\n{body}", 300)
    ok = (not e) and re.search(r"(?i)(successfully|created)", m) and not re.search(r"(?i)error|draft|fail|does not|mismatched|semantic", m)
    print(f"[{'OK ' if ok else 'NO '}] compile {name:26s} {time.perf_counter() - t0:5.1f}s" + ("" if ok else "\n" + tg.red(m)[:1600]), flush=True)
    return name, bool(ok)


def install(name):
    t0 = time.perf_counter()
    s, e, m = tg.gsql(f"USE GRAPH {tg.GRAPH}\nINSTALL QUERY {name}", 1800)
    ok = "failed: 0" in m and "succeeded: 1" in m
    print(f"[{'OK ' if ok else 'NO '}] install {name:26s} {time.perf_counter() - t0:5.1f}s" + ("" if ok else "\n" + tg.red(m)[-800:]), flush=True)
    return ok


if mode == "list":
    s, e, m = tg.gsql(f"USE GRAPH {tg.GRAPH}\nSHOW QUERY *")
    print(tg.red(m)[:3000])
    sys.exit(0)
bad = 0
for f in files():
    name, ok = create(f)
    if ok and mode == "install":
        ok = install(name)
    bad += not ok
    if not ok and mode == "install":
        print("stopping at first failure"); break
sys.exit(1 if bad else 0)
