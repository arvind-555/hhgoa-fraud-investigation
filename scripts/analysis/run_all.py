"""Run every analysis script in order and summarise PASS/FAIL. Usage: python run_all.py [--fast]
--fast skips the slow region/email link tables in 07.  Exit code = number of failing scripts."""
import subprocess
import sys
import time
from pathlib import Path

here = Path(__file__).resolve().parent
fast = "--fast" in sys.argv
scripts = ["01_card_id.py", "02_device_degree_asof.py", "03_temporal_filter.py", "04_closed_case_visibility.py",
           "05_ring_detection.py", "06_benchmark_leakage.py", "07_signal_validation.py"]
res = []
for s in scripts:
    t0 = time.time()
    cmd = [sys.executable, str(here / s)] + (["--skip-links"] if fast and s.startswith("07") else [])
    p = subprocess.run(cmd, cwd=here, capture_output=True, text=True)
    last = [ln for ln in p.stdout.splitlines() if "checks passed" in ln]
    res.append((s, p.returncode, last[-1].strip() if last else (p.stderr.strip().splitlines() or ["no output"])[-1], time.time() - t0))
    print(f"{'OK  ' if p.returncode == 0 else 'FAIL'} {s:34s} {res[-1][2]}  ({res[-1][3]:.0f}s)", flush=True)
bad = sum(1 for r in res if r[1] != 0)
print(f"\n{len(res) - bad}/{len(res)} scripts passed")
sys.exit(bad)
