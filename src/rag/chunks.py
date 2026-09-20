"""GraphRAG chunk builder (spec section 10). Produces TextChunk records; nothing is written here.

doc_type            chunk                                                              valid_from_epoch
closed_case         one per closed case: "<pattern> | <outcome> | <channel> | exposure band: analyst notes"   close_epoch (visible only once the case is closed)
policy              README Fraud Policy sections, rules R1-R10, actions/routes                             0
pattern             the five known fraud patterns + "not exhaustive"                                        0
format              answer-format rules                                                                     0

The agent's knowledge base is ONLY these documents (spec section 12.7). The README's list of the 20 exam cases, the case pack and every benchmark artefact are excluded; `assert_clean`
fails the build if any chunk mentions a benchmark case id. Closed-case notes are templated (392 normalised templates): they give wording and base-rate context, never a label for the case
under investigation. Vector embeddings are NOT created here (embedding model/dimension is an open decision); retrieval is lexical (BM25) over these chunks.
"""
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
from fraud_tools import policy  # noqa: E402

BENCH = re.compile(r"HHG-\d")
BANDS = [(100, "under $100"), (500, "$100-$500"), (1000, "$500-$1,000"), (2500, "$1,000-$2,500"), (float("inf"), "over $2,500")]


def band(x):
    return next(label for hi, label in BANDS if x < hi)


def _readme_sections():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    lines = text.splitlines()

    def between(start, stops):
        i = next(k for k, l in enumerate(lines) if l.startswith(start))
        j = next((k for k in range(i + 1, len(lines)) if any(lines[k].startswith(s) for s in stops)), len(lines))
        return lines[i + 1:j]
    return between("## The five known fraud patterns", ["## "]), between("# Fraud Policy", ["# Answer Format"]), between("# Answer Format", ["### Example"])


def _split_h3(lines, prefix):
    out, cur, name = [], [], None
    for l in lines:
        if l.startswith("### "):
            if name is not None:
                out.append((name, "\n".join(cur).strip()))
            name, cur = l[4:].strip(), []
        elif name is not None:
            cur.append(l)
    if name is not None:
        out.append((name, "\n".join(cur).strip()))
    return out


def static_chunks():
    pat, pol, fmt = _readme_sections()
    ch = []
    para = [p.strip() for p in "\n".join(pat).split("\n\n") if p.strip()]
    for i, p in enumerate(para):
        m = re.match(r"\*\*(\d)\. ([^*]+)\.\*\*", p)
        ref = f"pattern:{m.group(1)}" if m else f"pattern:note{i}"
        ch.append({"chunk_id": f"TX-{ref.replace(':', '-')}", "doc_type": "pattern", "source_ref": ref, "text": re.sub(r"\*\*|`", "", p), "valid_from_epoch": 0,
                   "pattern": "", "outcome": "", "channel": ""})
    for name, body in _split_h3(pol, "policy"):
        num = name.split(".")[0].strip()
        ref = f"policy:{num}"
        ch.append({"chunk_id": f"TX-policy-{num}", "doc_type": "policy", "source_ref": ref, "text": re.sub(r"\*\*|`", "", f"{name}. {body}"), "valid_from_epoch": 0,
                   "pattern": "", "outcome": "", "channel": ""})
    for rid, r in policy.RULES.items():
        txt = f"{rid}: {r['title']}. When: {r['when']} Actions: {'; '.join(r['actions'])}." + (f" Note: {r['note']}" if r.get("note") else "") + (f" Requires: {r['requires']}." if r.get("requires") else "")
        ch.append({"chunk_id": f"TX-policy-{rid}", "doc_type": "policy", "source_ref": f"policy:{rid}", "text": txt, "valid_from_epoch": 0, "pattern": "", "outcome": "", "channel": ""})
    ch.append({"chunk_id": "TX-policy-routes", "doc_type": "policy", "source_ref": "policy:routes",
               "text": "Approval routes. " + " ".join(f"{k}: {', '.join(v)}." for k, v in policy.ROUTES.items()) + " " + policy.EXECUTION, "valid_from_epoch": 0,
               "pattern": "", "outcome": "", "channel": ""})
    for name, body in _split_h3(fmt, "format"):
        ch.append({"chunk_id": "TX-format-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), "doc_type": "format", "source_ref": f"format:{name}",
                   "text": re.sub(r"\*\*|`", "", f"{name}. {body}"), "valid_from_epoch": 0, "pattern": "", "outcome": "", "channel": ""})
    return ch


def closed_case_chunks():
    from common import build_master, load_closed
    cc = load_closed()
    m = build_master()[["TransactionID", "channel"]].drop_duplicates("TransactionID").set_index("TransactionID")["channel"]
    first = cc["txn_ids"].str.split("|").str[0].astype("int64")
    chan = first.map(m).fillna("online")
    out = []
    for r, ch in zip(cc.itertuples(), chan):
        text = f"{r.pattern} | {r.outcome} | {ch} | exposure {band(float(r.exposure_usd))}: {str(r.analyst_notes)}"
        out.append({"chunk_id": f"CH-{r.case_id}", "doc_type": "closed_case", "source_ref": r.case_id, "text": text, "valid_from_epoch": int(r.close_ep),
                    "pattern": r.pattern, "outcome": r.outcome, "channel": ch})
    return out


def assert_clean(chunks):
    """Fail the build if any chunk could leak benchmark material."""
    bad = [c["chunk_id"] for c in chunks if BENCH.search(c["text"]) or BENCH.search(c["source_ref"])]
    if bad:
        raise AssertionError(f"benchmark case ids found in chunks: {bad[:5]}")
    if any("The 20 Cases" in c["text"] for c in chunks):
        raise AssertionError("the README case list section found in chunks")
    ids = [c["chunk_id"] for c in chunks]
    if len(ids) != len(set(ids)):
        raise AssertionError("duplicate chunk ids")
    return True


def build_all():
    ch = static_chunks() + closed_case_chunks()
    assert_clean(ch)
    return ch


if __name__ == "__main__":
    c = build_all()
    print(pd.Series([x["doc_type"] for x in c]).value_counts().to_dict())
