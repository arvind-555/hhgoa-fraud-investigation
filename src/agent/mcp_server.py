"""MCP server: exposes exactly the eight whitelisted investigation tools over stdio.

Launched BY THE RUNNER for one investigation:  python -m agent.mcp_server --as-of-epoch <N>   (or env FI_AS_OF_EPOCH).
The model can neither read nor change as_of: it is a process argument fixed by the runner, and no tool has an as_of/GSQL/graph parameter.
This server talks to TigerGraph only through fraud_tools.qclient (whitelisted installed fi_* queries on FraudInvestigation). The general-purpose
tigergraph-mcp (69 tools) is deliberately NOT mounted here: it would expose gsql/schema/drop tools (spec sections 11 and 12).
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mcp.server.mcpserver import MCPServer  # noqa: E402

from agent.gateway import ToolGateway  # noqa: E402
from agent.permissions import PermissionDenied, TOOLS  # noqa: E402
from fraud_tools.guards import LeakError, ToolError  # noqa: E402
from fraud_tools.tools import InvestigationSession  # noqa: E402

EXPOSED = ["get_transaction_context", "get_customer_history", "get_card_history", "find_shared_devices", "find_connected_entities",
           "find_prior_cases", "detect_fraud_patterns", "get_policy_context", "find_similar_cases", "retrieve_policy"]


def build(as_of_epoch, client=None):
    gw = ToolGateway(InvestigationSession(int(as_of_epoch), client))
    mcp = MCPServer("fraud-investigation-tools")

    def run(tool, **args):
        try:
            return json.dumps(gw.call(tool, args), default=str)
        except (PermissionDenied, ToolError, LeakError) as e:
            return json.dumps({"error": type(e).__name__, "message": str(e)[:300]})
        except Exception as e:                      # unexpected: type only, never the text (could carry connection details)
            return json.dumps({"error": type(e).__name__})

    @mcp.tool()
    def get_transaction_context(txn_id: str) -> str:
        """Attributes, card/customer/device/email/region context and temporal position of one visible transaction. Sentinels are null."""
        return run("get_transaction_context", txn_id=txn_id)

    @mcp.tool()
    def get_customer_history(customer_id: str, lookback_days: int = 200) -> str:
        """Customer's cards, spend profile and previously CLOSED cases (visible only after their close time)."""
        return run("get_customer_history", customer_id=customer_id, lookback_days=lookback_days)

    @mcp.tool()
    def get_card_history(card_id: str, hours: int = 48, max_rows: int = 100) -> str:
        """Recent transactions of a card (newest first) plus an all-history baseline (amount stats, product/channel/region histograms)."""
        return run("get_card_history", card_id=card_id, hours=hours, max_rows=max_rows)

    @mcp.tool()
    def find_shared_devices(card_id: str, days: int = 30) -> str:
        """Devices used by the card, with as-of sharing degree, evidence tier, ring suspicion, other customers/cards, visible confirmed-fraud customers. Hubs skipped."""
        return run("find_shared_devices", card_id=card_id, days=days)

    @mcp.tool()
    def find_connected_entities(card_id: str, days: int = 30) -> str:
        """Cards connected through eligible devices and low-degree recipient email/region entities. Hub/generic entities are listed as skipped, never linked."""
        return run("find_connected_entities", card_id=card_id, days=days)

    @mcp.tool()
    def find_prior_cases(card_id: str) -> str:
        """Closed cases (visible at close time) for the card, its customer and eligible shared devices, with match reasons. Memory, not proof."""
        return run("find_prior_cases", card_id=card_id)

    @mcp.tool()
    def detect_fraud_patterns(txn_id: str) -> str:
        """Evaluate the validated signals (S01-S15) for a transaction. Returns signals with tier/rating and independence; never a probability or verdict."""
        return run("detect_fraud_patterns", txn_id=txn_id)

    @mcp.tool()
    def get_policy_context(signals: Optional[list[str]] = None) -> str:
        """Fraud Policy v1.0 (R1-R10, actions, routes). Policy, not evidence."""
        return run("get_policy_context", **({"signals": signals} if signals else {}))

    @mcp.tool()
    def find_similar_cases(txn_id: str, k: int = 10) -> str:
        """GraphRAG: closed cases similar to this transaction (linked = shares the card/customer/eligible device; precedent = text resemblance). Visible-by-close-time only. Memory, not proof."""
        return run("find_similar_cases", txn_id=txn_id, k=k)

    @mcp.tool()
    def retrieve_policy(rule_ids: list[str]) -> str:
        """Fraud Policy text by exact reference (R1-R10, 3a, 3b, routes). Policy, not evidence."""
        return run("retrieve_policy", rule_ids=rule_ids)

    assert set(TOOLS) == set(EXPOSED)
    return mcp, gw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of-epoch", type=int, default=int(os.environ.get("FI_AS_OF_EPOCH", "0")))
    a = ap.parse_args()
    if a.as_of_epoch <= 0:
        sys.exit("--as-of-epoch (runner-supplied) is required")
    mcp, _ = build(a.as_of_epoch)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
