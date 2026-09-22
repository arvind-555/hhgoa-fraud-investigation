# HHGOA — Agentic Fraud Investigation

> **A TigerGraph-powered investigation agent that turns suspicious transaction alerts into evidence-backed investigations and policy-controlled next-best actions.**

[🚀 Live Demo](https://hhgoa-fraud-investigation.onrender.com) · [📦 GitHub](https://github.com/arvind-555/hhgoa-fraud-investigation)

---

## What We Built

Fraud investigation is rarely about a single suspicious transaction. The strongest signals often emerge from the **connections between customers, cards, devices, identities, transactions, and previous investigations**.

HHGOA turns those connections into an agentic investigation workflow.

When a suspicious transaction triggers an investigation, the agent:

1. **Investigates the transaction** using TigerGraph and time-aware evidence.
2. **Expands the investigation** across connected cards, customers, devices, identities, and transaction history.
3. **Retrieves relevant prior cases and policies** using GraphRAG.
4. **Assesses uncertainty** and determines whether the available evidence is sufficient.
5. **Requests controlled additional evidence** when required.
6. **Recommends the next-best action** according to the applicable fraud policy.
7. **Routes consequential actions** through the required approval level.
8. **Creates an auditable case record** containing evidence, findings, decisions, and actions.

### The Core Idea

> **The agent does not simply classify a transaction as fraud or legitimate. It investigates why, determines what is still uncertain, and decides what should happen next.**
