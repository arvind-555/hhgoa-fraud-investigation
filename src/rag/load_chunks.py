"""Build the GraphRAG chunks and upsert them (TextChunk + DESCRIBES) into FraudInvestigation. Idempotent. Usage: python src/rag/load_chunks.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fraud_tools.qclient import QueryClient  # noqa: E402
from rag.chunks import build_all  # noqa: E402
from rag.store import ChunkStore  # noqa: E402

if __name__ == "__main__":
    ch = build_all()
    t0 = time.time()
    n = ChunkStore().upsert(ch)
    print(f"upserted {n} chunks in {time.time() - t0:.1f}s", flush=True)
    time.sleep(5)
    c = QueryClient()
    for dt in ("closed_case", "policy", "pattern", "format"):
        r = c.run("fi_text_chunks", as_of=10 ** 9, doc_type=dt, channel="", max_chunks=8000)
        rows = r.get("S", [])
        print(dt, "visible rows:", len(rows), "with DESCRIBES case:", sum(1 for x in rows if x.get("case_id")), "max_epoch_seen:", r.get("max_epoch_seen"))
