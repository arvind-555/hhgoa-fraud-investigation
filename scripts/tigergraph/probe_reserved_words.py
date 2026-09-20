"""Find which planned attribute/type names are GSQL reserved words (parse-only: jobs are created, never run).
Uses one throwaway graph (dropped afterwards); never touches Transaction_Fraud; prints no secrets."""
import json, re, sys, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = "hhg_p8_names"
env = {}
for ln in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^([A-Z_]+)=(.*)$", ln.strip())
    if m:
        env[m.group(1)] = m.group(2)
HOST, SECRET = env["TG_HOST"].rstrip("/"), env["TG_SECRET"]


def http(method, path, data=None, headers=None, timeout=300):
    h = dict(headers or {})
    if isinstance(data, dict):
        data = json.dumps(data).encode(); h["Content-Type"] = "application/json"
    elif isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(HOST + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


s, t = http("POST", "/gsql/v1/tokens", {"secret": SECRET})
TOKEN = json.loads(t)["token"]


def gsql(text):
    assert "transaction_fraud" not in text.lower()
    s, t = http("POST", "/gsql/v1/statements", text, {"Authorization": "Bearer " + TOKEN, "Content-Type": "text/plain"})
    try:
        j = json.loads(t); return j.get("message", t).replace(TOKEN, "<t>").replace(SECRET, "<s>")
    except Exception:
        return t


ATTRS = ["txn_id", "epoch", "ts", "amount", "product_cd", "channel", "risk_score", "card_id", "customer_id", "addr1", "addr2", "dist1", "dist2",
         "has_identity", "id_15", "proxy", "device_type", "p_email", "r_email", "c1", "c2", "c4", "c8", "c10", "d1", "d2", "d3", "d4", "d5", "d10", "d15",
         "m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8", "m9", "v51", "v52", "v79", "v93", "v94", "v217", "v258", "v264", "v308",
         "role", "via", "score", "method", "reason", "status", "verdict", "pattern", "outcome", "text", "summary", "revision", "os", "browser", "screen",
         "country", "domain", "channel", "amount", "first_epoch", "gap_s", "is_first", "as_of", "opened_at", "closed_at"]
TYPES = ["Customer", "Card", "Transaction", "DeviceProfile", "EmailDomain", "BillingRegion", "ClosedCase", "Case", "TextChunk", "FI_Case",
         "OWNS", "MADE", "NEXT", "FROM_DEVICE", "SEEN_ON", "PURCHASER_EMAIL", "RECIPIENT_EMAIL", "BILLED_IN", "CLOSED_ON_CARD", "CLOSED_ON_CUSTOMER",
         "CLOSED_INVOLVES", "CLOSED_CONNECTED_TO", "CASE_ON_CARD", "CASE_TXN", "CASE_CONNECTED_TO", "CASE_CITES_DEVICE", "SIMILAR_CASE", "DESCRIBES",
         "OWNED_BY", "MADE_BY", "PREV", "DEVICE_OF", "SEEN_BY", "PURCHASER_OF", "RECIPIENT_OF", "BILLED_HERE", "TXN_IN_CASE", "SIMILAR_FROM", "DESCRIBED_BY"]

created = False
bad_attr, bad_type = [], []
try:
    print(gsql(f"CREATE GRAPH {TMP} ()").strip()); created = True
    n = 0
    for a in dict.fromkeys(ATTRS):                       # attribute names, one parse-only job each
        n += 1
        m = gsql(f"USE GRAPH {TMP}\nCREATE SCHEMA_CHANGE JOB pa{n} FOR GRAPH {TMP} {{ ADD VERTEX PA{n} (PRIMARY_ID id STRING, {a} STRING); }}")
        if "Successfully created schema change jobs" not in m:
            bad_attr.append((a, re.sub(r"\s+", " ", m)[-110:]))
    for ty in dict.fromkeys(TYPES):                      # vertex-type-name test (edge names tested as vertex names is a fair keyword test)
        n += 1
        m = gsql(f"USE GRAPH {TMP}\nCREATE SCHEMA_CHANGE JOB pt{n} FOR GRAPH {TMP} {{ ADD VERTEX {ty} (PRIMARY_ID id STRING, x INT); }}")
        if "Successfully created schema change jobs" not in m:
            bad_type.append((ty, re.sub(r"\s+", " ", m)[-110:]))
finally:
    if created:
        print(gsql(f"DROP GRAPH {TMP} CASCADE").strip())
print("RESERVED/INVALID ATTRIBUTE NAMES:", bad_attr)
print("RESERVED/INVALID TYPE NAMES:", bad_type)
