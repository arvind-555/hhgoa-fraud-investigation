"""GraphIO: the ONLY door for case writing and case validation reads. Built on the guarded loading client (graph fixed to FraudInvestigation,
Transaction_Fraud refused, secrets redacted). Nothing here is reachable from the LLM/agent tools.

Reads : single-vertex lookups of a fixed type list (+ the FROM_DEVICE edges of a transaction).
Writes: FI_Case vertices and the CASE_ON_CARD / CASE_TXN / CASE_CONNECTED_TO / CASE_CITES_DEVICE / SIMILAR_CASE edges that start at an FI_Case. Any other
        vertex or edge type is refused, so source Transaction/Customer/Card/Device/ClosedCase data can never be overwritten.
Deletes: only FI_Case vertices whose id starts with CASE-TEST- (test clean-up) and the outgoing case edges of an FI_Case being revised.
"""
import json
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "ingestion"))
import tg  # noqa: E402  (guarded client: fixed graph, refuses Transaction_Fraud, redacts secrets)

GRAPH = tg.GRAPH
READ_TYPES = {"Transaction", "Card", "Customer", "DeviceProfile", "ClosedCase", "FI_Case"}
CASE_EDGES = {"CASE_ON_CARD": "Card", "CASE_TXN": "Transaction", "CASE_CONNECTED_TO": "Card", "CASE_CITES_DEVICE": "DeviceProfile", "SIMILAR_CASE": "ClosedCase"}
CASE_VERTEX = "FI_Case"
TEST_PREFIX = "CASE-TEST-"


class GraphIOError(Exception):
    pass


def _q(x):
    return urllib.parse.quote(str(x), safe="")


class GraphIO:
    def __init__(self, retries=3):
        self.retries = retries
        self.reads = 0
        self.writes = 0

    def _req(self, method, path, data=None):
        last = None
        for i in range(self.retries):
            try:
                s, t = tg.req(method, path, data)
                if s in (500, 502, 503) or "network error" in str(t)[:60]:
                    last = f"HTTP {s} {str(t)[:80]}"
                    time.sleep(1.5 * (i + 1))
                    continue
                return s, t
            except Exception as e:                       # network resets are transient on this workspace
                last = tg.red(str(e))[:120]
                time.sleep(1.5 * (i + 1))
        raise GraphIOError(f"graph request failed after {self.retries} tries: {last}")

    # ---------------------------------------------------------------------------------------------- reads
    def get_vertex(self, vtype, vid):
        if vtype not in READ_TYPES:
            raise GraphIOError(f"vertex type not readable: {vtype}")
        self.reads += 1
        s, t = self._req("GET", f"/restpp/graph/{GRAPH}/vertices/{vtype}/{_q(vid)}")
        try:
            j = json.loads(t)
        except Exception:
            raise GraphIOError(f"unparseable response (HTTP {s})")
        if "is not a valid vertex id" in str(j.get("message", "")):
            return None                                   # TigerGraph answers HTTP 200 + error for a missing id of some types (e.g. an empty FI_Case)
        if s != 200 or j.get("error"):
            raise GraphIOError(tg.red(f"read failed HTTP {s}: {j.get('message', '')[:120]}"))
        return j["results"][0]["attributes"] if j.get("results") else None

    def txn_devices(self, txn_id):
        self.reads += 1
        s, t = self._req("GET", f"/restpp/graph/{GRAPH}/edges/Transaction/{_q(txn_id)}/FROM_DEVICE")
        j = json.loads(t)
        if s != 200 or j.get("error"):
            raise GraphIOError(tg.red(f"edge read failed HTTP {s}"))
        return sorted(e["to_id"] for e in j.get("results", []))

    def case_edge_counts(self, graph_case_id):
        out = {}
        for et in CASE_EDGES:
            self.reads += 1
            s, t = self._req("GET", f"/restpp/graph/{GRAPH}/edges/{CASE_VERTEX}/{_q(graph_case_id)}/{et}")
            j = json.loads(t)
            out[et] = sorted((e["to_type"], str(e["to_id"]), json.dumps(e.get("attributes", {}), sort_keys=True)) for e in j.get("results", []))
        return out

    # ---------------------------------------------------------------------------------------------- writes
    @staticmethod
    def _attr(v):
        return {"value": v}

    def upsert_case(self, graph_case_id, attrs, edges):
        """attrs: FI_Case attributes; edges: {edge_type: [(target_id, {attr: value}), ...]}. One request = one atomic upsert."""
        for et in edges:
            if et not in CASE_EDGES:
                raise GraphIOError(f"edge type not writable: {et}")
        body = {"vertices": {CASE_VERTEX: {graph_case_id: {k: self._attr(v) for k, v in attrs.items()}}}, "edges": {}}
        if any(edges.values()):
            body["edges"][CASE_VERTEX] = {graph_case_id: {et: {CASE_EDGES[et]: {str(t): {k: self._attr(v) for k, v in a.items()} for t, a in tgts}}
                                                          for et, tgts in edges.items() if tgts}}
        self.writes += 1
        s, t = self._req("POST", f"/restpp/graph/{GRAPH}", body)
        try:
            j = json.loads(t)
        except Exception:
            raise GraphIOError(f"unparseable upsert response (HTTP {s})")
        if s != 200 or j.get("error"):
            raise GraphIOError(tg.red(f"upsert failed HTTP {s}: {str(j.get('message', ''))[:200]}"))
        return j.get("results", [{}])[0]

    def delete_case_edges(self, graph_case_id):
        for et in CASE_EDGES:
            self.writes += 1
            s, t = self._req("DELETE", f"/restpp/graph/{GRAPH}/edges/{CASE_VERTEX}/{_q(graph_case_id)}/{et}")
            if s != 200:
                raise GraphIOError(tg.red(f"edge delete failed HTTP {s}"))

    def delete_test_case(self, graph_case_id):
        if not str(graph_case_id).startswith(TEST_PREFIX):
            raise GraphIOError("only CASE-TEST- vertices may be deleted")
        self.delete_case_edges(graph_case_id)
        self.writes += 1
        s, t = self._req("DELETE", f"/restpp/graph/{GRAPH}/vertices/{CASE_VERTEX}/{_q(graph_case_id)}")
        if s != 200:
            raise GraphIOError(tg.red(f"vertex delete failed HTTP {s}"))
