"""Tool permissions: what the agent may call, with which arguments, in which state. Nothing else exists for it."""
import re

from .schema import State

ID = {"txn": r"^\d{7}$", "card": r"^C\d{5}-K\d$", "customer": r"^C\d{5}$"}

# name -> {arg: ("id", kind) | ("int", lo, hi)}, required args
TOOLS = {
    "get_transaction_context": {"args": {"txn_id": ("id", "txn")}, "required": ["txn_id"], "readonly": True},
    "get_customer_history": {"args": {"customer_id": ("id", "customer"), "lookback_days": ("int", 1, 200)}, "required": ["customer_id"], "readonly": True},
    "get_card_history": {"args": {"card_id": ("id", "card"), "hours": ("int", 1, 4800), "max_rows": ("int", 1, 500)}, "required": ["card_id"], "readonly": True},
    "find_shared_devices": {"args": {"card_id": ("id", "card"), "days": ("int", 1, 30)}, "required": ["card_id"], "readonly": True},
    "find_connected_entities": {"args": {"card_id": ("id", "card"), "days": ("int", 1, 30)}, "required": ["card_id"], "readonly": True},
    "find_prior_cases": {"args": {"card_id": ("id", "card")}, "required": ["card_id"], "readonly": True},
    "detect_fraud_patterns": {"args": {"txn_id": ("id", "txn")}, "required": ["txn_id"], "readonly": True},
    "get_policy_context": {"args": {"signals": ("idlist",)}, "required": [], "readonly": True},
    "find_similar_cases": {"args": {"txn_id": ("id", "txn"), "k": ("int", 1, 20)}, "required": ["txn_id"], "readonly": True},
    "retrieve_policy": {"args": {"rule_ids": ("rulelist",)}, "required": ["rule_ids"], "readonly": True},
}

# arguments/capabilities that must never be accepted from the model (rejected by name before anything else)
FORBIDDEN_ARGS = {"as_of", "as_of_epoch", "from_epoch", "to_epoch", "before_epoch", "gsql", "query", "cypher", "graph", "graph_name", "vertex_type", "edge_type",
                  "risk_score", "min_risk", "order_by", "sort", "filter", "where", "limit", "token", "secret", "host", "label", "outcome"}
NEVER_EXPOSED_TOOLS = ["tigergraph__gsql", "run_query", "run_installed_query", "search_top_k_similarity", "clear_graph_data", "drop_graph", "upsert_vertices",
                       "upsert_edges", "get_schema", "load_data", "leak_check", "write_case"]   # write_case is orchestrator-internal, not an LLM tool

# which tools each state may call (the orchestrator enforces it; the MCP server exposes the union)
STATE_TOOLS = {
    State.INITIAL_INVESTIGATION: set(TOOLS),
    State.ADDITIONAL_EVIDENCE_REQUEST: {"get_policy_context", "get_card_history"},
    State.NEXT_BEST_ACTION: {"retrieve_policy"},
}
_SIG = re.compile(r"^S\d{2}[ab]?$")
_RULE = re.compile(r"^(R([1-9]|10)|3a|3b|routes)$")


class PermissionDenied(Exception):
    pass


# Optional arguments and the value the tool uses when they are omitted (must equal the InvestigationSession defaults; tests compare them with the real signatures).
DEFAULTS = {"get_customer_history": {"lookback_days": 200}, "get_card_history": {"hours": 48, "max_rows": 100}, "find_shared_devices": {"days": 30},
            "find_connected_entities": {"days": 30}, "find_similar_cases": {"k": 10}}
LIST_ARGS = ("signals", "rule_ids")           # sets of references: order and duplicates carry no meaning


def canonical_key(tool, args):
    """Key under which two calls are the SAME investigation call. Applied only to arguments that ALREADY passed `validate_call` (forbidden / unknown arguments and bad values are refused
    before this point, and as_of / epochs / filters can never appear). Harmless differences collapse: argument order, an optional argument passed with the tool's own default value
    (the same value the tool would use if it were omitted), and the order / repetition of a reference list. Anything that can change the result (a different value, e.g. k=5 vs the default
    10, or a different lookback) keeps a different key."""
    d = DEFAULTS.get(tool, {})
    items = []
    for k, v in args.items():
        if k in d and v == d[k]:
            continue
        if k in LIST_ARGS and isinstance(v, list):
            v = tuple(sorted(set(v)))
        elif isinstance(v, list):
            v = tuple(v)
        items.append((k, v))
    return (tool, tuple(sorted(items)))


def validate_call(tool, args, state=None):
    """Raise PermissionDenied unless (tool, args) is a whitelisted call. Returns normalised args."""
    if tool not in TOOLS:
        raise PermissionDenied(f"tool not exposed: {tool!r}")
    if state is not None and tool not in STATE_TOOLS.get(state, set()):
        raise PermissionDenied(f"{tool} is not permitted in state {getattr(state, 'value', state)}")
    spec = TOOLS[tool]
    args = dict(args or {})
    bad = [k for k in args if k.lower() in FORBIDDEN_ARGS]
    if bad:
        raise PermissionDenied(f"argument(s) not accepted from the agent: {sorted(bad)}")
    unknown = [k for k in args if k not in spec["args"]]
    if unknown:
        raise PermissionDenied(f"{tool}: unknown argument(s) {sorted(unknown)}")
    missing = [k for k in spec["required"] if k not in args]
    if missing:
        raise PermissionDenied(f"{tool}: missing argument(s) {missing}")
    for k, v in args.items():
        t = spec["args"][k]
        if t[0] == "id":
            if not isinstance(v, str) or not re.match(ID[t[1]], v):
                raise PermissionDenied(f"{tool}.{k}: not a valid {t[1]} id")
        elif t[0] == "int":
            if isinstance(v, bool) or not isinstance(v, int) or not t[1] <= v <= t[2]:
                raise PermissionDenied(f"{tool}.{k}: integer in [{t[1]}, {t[2]}] required")
        elif t[0] == "rulelist":
            if not isinstance(v, list) or not v or len(v) > 12 or not all(isinstance(x, str) and _RULE.match(x) for x in v):
                raise PermissionDenied(f"{tool}.{k}: list of policy references (R1-R10, 3a, 3b, routes) required")
        else:
            if not isinstance(v, list) or not all(isinstance(x, str) and _SIG.match(x) for x in v):
                raise PermissionDenied(f"{tool}.{k}: list of signal ids required")
    return args
