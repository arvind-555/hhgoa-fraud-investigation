"""Deterministic, read-only investigation tools over the FraudInvestigation graph (Phase 9A).

Public entry point: `fraud_tools.tools.InvestigationSession(as_of_epoch)`.
The session (held by the trusted runner) fixes `as_of`; tools take ids only and can reach nothing but the whitelisted fi_* queries.
"""
