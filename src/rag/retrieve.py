"""GraphRAG retrieval (pure functions). Structured seeds + lexical BM25 over TextChunks that the graph query already filtered to `valid_from_epoch <= as_of`.

1. structured seeds  : closed cases related to the card/customer/eligible shared device (from find_prior_cases) that are visible at as_of -> label `linked`;
2. text half         : BM25 over the visible closed-case chunks of the same channel, query built from the case's own non-label features -> label `precedent`;
3. every hit carries case id, pattern, outcome, close_epoch (visible by close time) and a short snippet.
The query never contains risk_score, labels of the current case or benchmark ids. Closed-case notes are templated: they are wording/base-rate context, not proof.
No vector search (embedding model undecided); the interface leaves room to fuse a vector half later.
"""
import math
import re

_TOK = re.compile(r"[a-z0-9_]+")
STOP = set("a an and are as at be by for from in is it its of on or that the this to was were with not no has have had been than then also into over under".split())
K1, B = 1.5, 0.75
SIGNAL_TERMS = {"S01": "device shared several customers new device proxy ring", "S02a": "device shared another customer confirmed fraud new device",
                "S02b": "device shared another customer confirmed fraud new device", "S06": "card testing small online authorizations then larger purchase",
                "S07": "burst repeated purchases same card within hours", "S08": "unusual amount online purchase inconsistent with cardholder history",
                "S09": "new product category never used", "S10": "new device", "S11": "proxy anonymous", "S12": "new device proxy", "S15": "card present billing region no history"}


def tokens(text):
    return [t for t in _TOK.findall(text.lower()) if t not in STOP and len(t) > 1]


def build_query(channel, fired):
    """Deterministic query text from the case's own features (no labels, no scores)."""
    base = "online card not present purchases unrecognized" if channel == "online" else "card present in person purchases billing region unrecognized"
    return base + " " + " ".join(SIGNAL_TERMS[s] for s in sorted(fired) if s in SIGNAL_TERMS)


def bm25(query, docs):
    """docs: list of (doc_id, text). Returns {doc_id: score} (only positive scores)."""
    q = tokens(query)
    toks = {i: tokens(t) for i, t in docs}
    n = len(docs)
    avg = (sum(len(v) for v in toks.values()) / n) if n else 1.0
    df = {}
    for v in toks.values():
        for w in set(v):
            df[w] = df.get(w, 0) + 1
    out = {}
    for i, v in toks.items():
        tf = {}
        for w in v:
            tf[w] = tf.get(w, 0) + 1
        s = 0.0
        for w in set(q):
            if w in tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * tf[w] * (K1 + 1) / (tf[w] + K1 * (1 - B + B * len(v) / avg))
        if s > 0:
            out[i] = s
    return out


def similar_cases(rows, seeds, query, k=10, max_linked=5):
    """rows: visible closed-case chunk rows (dicts with chunk_id, text, case_id, close_ep, pattern, outcome, channel). seeds: {case_id: [match_reasons]}.
    Returns the ranked hits (linked seeds first, then BM25 precedents), at most k."""
    rows = [r for r in rows if r.get("case_id")]
    by_case = {r["case_id"]: r for r in rows}
    scores = bm25(query, [(r["case_id"], r["text"]) for r in rows])
    linked = sorted((c for c in seeds if c in by_case), key=lambda c: (-by_case[c]["close_ep"], c))[:max_linked]
    hits = []
    for c in linked:
        hits.append(_hit(by_case[c], scores.get(c, 0.0), "linked", seeds[c]))
    rest = sorted((c for c in scores if c not in set(linked)), key=lambda c: (-scores[c], c))
    for c in rest[:max(0, min(k, 20) - len(hits))]:
        hits.append(_hit(by_case[c], scores[c], "precedent", []))
    return hits


def _hit(r, score, label, reasons):
    return {"chunk_id": r["chunk_id"], "case_id": r["case_id"], "label": label, "score": round(score, 4), "pattern": r["pattern"], "outcome": r["outcome"],
            "channel": r["channel"], "close_epoch": int(r["close_ep"]), "match_reasons": sorted(reasons), "snippet": r["text"][:240]}


def policy_chunks(rows, refs):
    by = {r["source_ref"]: r for r in rows}
    return [{"source_ref": f"policy:{x}", "chunk_id": by[f"policy:{x}"]["chunk_id"], "text": by[f"policy:{x}"]["text"][:1200]} for x in refs if f"policy:{x}" in by]
