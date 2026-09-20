"""ChunkStore: loads TextChunk vertices and DESCRIBES edges (TextChunk -> ClosedCase) into FraudInvestigation. Restricted: it can write NOTHING else
(no other vertex or edge type), so source facts can never be touched. Upserts are idempotent (same chunk id = same vertex)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "ingestion"))
import tg  # noqa: E402

ATTRS = ("doc_type", "source_ref", "text", "valid_from_epoch", "pattern", "outcome", "channel")
BATCH = 300


class ChunkStoreError(Exception):
    pass


class ChunkStore:
    def __init__(self, req=None):
        self._req = req or tg.req
        self.batches = 0

    def upsert(self, chunks):
        """chunks: list of chunk dicts (see rag.chunks). closed_case chunks also get a DESCRIBES edge to ClosedCase <source_ref>."""
        for i in range(0, len(chunks), BATCH):
            part = chunks[i:i + BATCH]
            body = {"vertices": {"TextChunk": {c["chunk_id"]: {k: {"value": c[k]} for k in ATTRS} for c in part}}, "edges": {}}
            edges = {c["chunk_id"]: {"DESCRIBES": {"ClosedCase": {c["source_ref"]: {}}}} for c in part if c["doc_type"] == "closed_case"}
            if edges:
                body["edges"]["TextChunk"] = edges
            s, t = self._req("POST", f"/restpp/graph/{tg.GRAPH}", body)
            try:
                j = json.loads(t)
            except Exception:
                raise ChunkStoreError(f"unparseable upsert response (HTTP {s})")
            if s != 200 or j.get("error"):
                raise ChunkStoreError(tg.red(f"upsert failed HTTP {s}: {str(j.get('message', ''))[:200]}"))
            self.batches += 1
        return len(chunks)
