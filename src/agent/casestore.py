"""Case write (Phase 9B): local staging only. The graph write (Case-family vertices/edges, spec 11) is NOT implemented yet (Phase 10)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class LocalCaseStore:
    def __init__(self, folder=None):
        self.folder = Path(folder) if folder else ROOT / "data" / "runs" / "phase9b" / "cases"
        self.folder.mkdir(parents=True, exist_ok=True)

    def write(self, record):
        (self.folder / f"{record['case_id']}.json").write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
