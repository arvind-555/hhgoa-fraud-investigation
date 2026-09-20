"""Copy the full agent records of a completed deterministic run into demo/records/ for the UI, after checking that they agree with cases/.

Usage: python src/ui_api/export_records.py <run_dir>     (run_dir holds records/HHG-###.json written by the trusted runner)
The UI needs the trace, evidence ratings, uncertainty, exposure scope and graph refs that the README-format answer files do not contain.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from ui_api.service import CaseService  # noqa: E402


def main(run_dir):
    src, dst = Path(run_dir) / "records", ROOT / "demo" / "records"
    dst.mkdir(parents=True, exist_ok=True)
    svc = CaseService(records_dir=dst)
    for cid in svc.ids():
        rec = json.loads((src / f"{cid}.json").read_text(encoding="utf-8"))
        (dst / f"{cid}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    svc = CaseService(records_dir=dst)
    bad = [c for c in svc.ids() if not svc.get_case(c)["integrity"]["ok"]]
    print(f"exported {len(svc.ids())} records; disagreeing with cases/: {bad or 'none'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
