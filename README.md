# HHGOA — Agentic Fraud Investigation

> **A TigerGraph-powered investigation agent that turns suspicious transaction alerts into evidence-backed investigations and policy-controlled next-best actions.**

[Live Demo](https://hhgoa-fraud-investigation.onrender.com) · [GitHub Repository](https://github.com/arvind-555/hhgoa-fraud-investigation)

---

## What We Built

Fraud investigation is rarely about one suspicious transaction in isolation. The strongest signals come from how a transaction connects to everything around it: the **customer**, their **card**, the **device** it was made from, its **identity and email fingerprint**, its **billing region**, and any **previous investigations** that touched the same entities.

HHGOA turns those connections into an agentic investigation workflow. Given a triggering alert, the agent:

1. **Investigates the transaction** using TigerGraph and point-in-time evidence.
2. **Expands the investigation** across connected cards, customers, devices, and prior transaction history.
3. **Retrieves relevant prior cases and policy text** using GraphRAG.
4. **Assesses uncertainty** and determines whether the available evidence is sufficient to reach a conclusion.
5. **Requests controlled additional evidence** when it isn't.
6. **Recommends a next-best action** under the applicable fraud policy.
7. **Routes consequential actions** through the required approval level.
8. **Creates an auditable case record** — evidence, findings, decision, and actions — and writes it to the graph.

### The Core Idea

> **The agent does not simply classify a transaction as fraud or legitimate. It investigates why, determines what is still uncertain, and decides what should happen next.**

---

## Architecture

```
                         FRAUD SIGNAL
                              │
                              ▼
                    ┌───────────────────┐
                    │   Agent Trigger   │
                    └─────────┬─────────┘
                              │
                              ▼
                 ┌────────────────────────┐
                 │      TigerGraph        │
                 │                        │
                 │ Customer ↔ Card        │
                 │ Card ↔ Transaction     │
                 │ Transaction ↔ Device   │
                 │ Identity / Email /     │
                 │ Billing Region / Cases │
                 └───────────┬────────────┘
                             │
                             ▼
                 ┌────────────────────────┐
                 │       GraphRAG         │
                 │                        │
                 │ Prior Cases             │
                 │ Fraud Patterns          │
                 │ Policies & Rules        │
                 └───────────┬────────────┘
                             │
                             ▼
                 ┌────────────────────────┐
                 │ Investigation Agent    │
                 │                        │
                 │ Evidence gathering      │
                 │ Evidence synthesis      │
                 │ Uncertainty assessment  │
                 │ Tool selection          │
                 └───────────┬────────────┘
                             │
                    ┌────────┴────────┐
                    │                 │
              More evidence?     Sufficient?
                    │                 │
                    ▼                 ▼
          Controlled evidence     Next-Best
               request             Action
                                      │
                                      ▼
                           ┌──────────────────┐
                           │ Policy Authority │
                           │                  │
                           │ Actions          │
                           │ Approval routes  │
                           │ Safety gates     │
                           └────────┬─────────┘
                                    │
                                    ▼
                             Case Record
                         Evidence + Findings
                         Decision + Actions
```

### Component Responsibilities

| Component | Responsibility |
|---|---|
| **TigerGraph** | Stores the connected entity graph (customers, cards, transactions, devices, identity, closed cases) and answers whitelisted, point-in-time queries. |
| **GraphRAG** | Retrieves relevant prior closed cases, fraud-pattern descriptions, and policy text from `TextChunk` vertices, filtered to what existed as of the investigation's own timestamp. |
| **Investigation Agent** | Orchestrates the investigation: gathers evidence via graph tools, synthesizes it, assesses uncertainty, and decides whether more evidence is needed. |
| **Policy Authority** | A deterministic decision layer, separate from the agent's reasoning, that maps evidence and uncertainty to a verdict, actions, approval routes, and SAR filing decisions. |
| **Case Memory** | The auditable output: an `FI_Case` graph vertex plus a JSON case record containing evidence, findings, decision, and actions. |

---

## Why TigerGraph?

The investigation follows a connected-entity model:

```
Customer → Card → Transaction → Device
              ↕
   Email Domain · Billing Region · Previous Cases · Connected Cards
```

A transaction is not investigated alone. The graph lets the agent ask investigation questions that a row of transaction data cannot answer on its own:

- Does this card's device also appear on other customers' cards? (shared-device rings)
- Which other cards are connected to this one, and how?
- What did this card do before and after the flagged transaction?
- Has any of these entities been part of a previous closed investigation?
- What fraud patterns and policy sections are relevant to what was just found?
- What evidence actually existed at the time the case was opened — not what exists now?

**Temporal safety (`as_of`).** Every graph read is scoped to the investigation's own `as_of` timestamp, fixed by the runner before the agent starts and never supplied by the caller. No tool can see a transaction, a closed case, or a policy chunk that postdates the moment the case was opened, which rules out label leakage and hindsight bias in the retrieved evidence.

---

## Safety & Agentic Design

The system is built as three separated layers:

1. **Agentic orchestration / reasoning** — decides which graph tools to call, in what order, and when enough evidence has been gathered.
2. **Graph-derived evidence** — the facts the tools return; nothing is inferred or assumed beyond what the graph and policy documents state.
3. **Deterministic policy authority** — a fixed decision matrix that maps evidence strength and uncertainty to a verdict, next-best action, and approval route.

**The LLM is not the source of truth for fraud decisions.** An optional LLM-driven investigator can be used to choose which graph tools to call and in what order, but the verdict, the action, and the SAR decision always come from the deterministic policy layer, not from model output. This was verified directly: an LLM-orchestrated run reproduces the same verdict, actions, and case authority as the deterministic pipeline on the same case.

Concrete safeguards:

- **Whitelisted tools only.** The agent (and any LLM investigator) can call exactly ten read-only graph tools; nothing else is exposed.
- **Restricted parameters.** Every tool argument is validated against an id pattern or an integer range before the call is made; out-of-range or malformed values are refused, never coerced.
- **No arbitrary GSQL.** Tools run pre-installed, parameterized queries. There is no free-form query path and no schema/admin access.
- **`as_of` enforcement.** Temporal, filter, and internal arguments (`as_of`, epochs, `risk_score`, `gsql`, `token`, `secret`, and others) are on a forbidden-argument list and are rejected by name before anything else is checked.
- **Deterministic policy gates.** When the investigation is uncertain, whether additional evidence may be requested — and what type — is decided by policy, not by the model's preference.
- **Approval routes.** Every recommended action carries a route (automatic, team-lead, or fraud-manager approval); nothing is executed by the system itself.
- **Controlled graph writes.** Case writes are restricted to `FI_Case` vertices and their own edges, are content-hashed for idempotency, and can never touch source transaction, customer, card, or device data.
- **Structured case record.** Every case output has a fixed schema: status, verdict, evidence, uncertainty, next-best actions, SAR decision, and graph references.
- **Controlled evidence gathering.** Additional evidence (e.g., customer verification) is requested only when policy requires it, and any model-suggested request is deterministically gated before it can run.

---

## Validation & Engineering

These are the validated facts about the pipeline, not a claim of judged accuracy — the official benchmark answer key was never available to us.

- **20/20** benchmark cases processed end-to-end through the pipeline.
- **20/20** `FI_Case` records validated (structure, exposure recomputation, temporal consistency).
- **20/20** SAR records validated for internal consistency with their case facts.
- **0** temporal-leakage violations.
- **0** policy violations.
- **0** graph-invariant violations.
- Deterministic outputs are **reproducible**: re-running the pipeline reproduces the same case files.
- Backend, frontend, and live-TigerGraph integration tests all pass.

Dataset scale the investigation graph is built on:

| Entity | Count |
|---|---|
| Transactions | 590,742 |
| Customers | 13,553 |
| Cards | 14,317 |
| Device profiles | 9,706 |
| Closed investigations | 5,565 |

We do not report a `fraud_probability` figure for these cases: the only labelled outcomes are the closed investigations, which are a selected population (skewed toward confirmed fraud) rather than a representative sample of the alert stream, and the calibration validation gates fail for every evidence-strength class on the data available. Rather than fabricate a number, the field is left `null` and the decision is driven by evidence strength and independent-source counts instead.

---

## Live Investigation

The deployed demo includes a **read-only live investigation preview**: a judge can pick one of the 20 benchmark cases and have the agent investigate it right now, against the live TigerGraph instance, rather than only replaying a stored result.

The live preview:

- Runs the same deterministic investigation pipeline against **live TigerGraph** — it is not a replay of a stored answer.
- Uses **server-side case metadata only** (the trigger, transaction, and timestamp come from the server's own case pack; the caller supplies nothing but which of the 20 cases to run).
- Performs **no `FI_Case` graph write** — it is a preview of the investigation process, not a case-filing action.
- Is **single-flight and rate-limited**, so only one live investigation runs at a time and results are shared/cached briefly to prevent request stacking.
- Compares its live result's content hash against the corresponding stored `FI_Case` vertex and reports whether they match.

HHG-014 (a multi-card, shared-device ring case) is used as the reference demonstration case in this project's own testing, because it exercises the widest evidence path of the twenty.

Try it: [Live Demo](https://hhgoa-fraud-investigation.onrender.com)

---

## Project Structure

```
cases/     Submitted investigation answer files (HHG-001 … HHG-020)
config/    Calibration artifact and .env.example templates (TigerGraph, LLM providers)
demo/      Stored per-case investigation records used to power the UI's replay mode
docs/      Design notes, audits, dataset schema, and the preserved hackathon-spec.md
graph/     TigerGraph schema, installed GSQL queries, and load scripts
scripts/   Dataset analysis and TigerGraph provisioning scripts
src/       Agent, fraud-detection tools, GraphRAG, calibration, and the UI API
tests/     Backend test suite (agent, tools, RAG, UI API, live TigerGraph integration)
ui/        React/TypeScript frontend (investigation dashboard, replay, live preview)
```

---

## Running Locally

**Backend**

```bash
python -m unittest discover -s tests
python src/ui_api/server.py
```

The UI API server serves the investigation dashboard's read-only API (and the built frontend, once `ui/dist` exists) on `http://127.0.0.1:8787` by default.

**Frontend**

```bash
cd ui
npm install
npm run dev      # local development server
npm run build    # production build to ui/dist
npm run test     # frontend test suite
```

**TigerGraph configuration**

Copy `config/tigergraph.example.env` to a git-ignored `.env` at the repository root and fill in your own workspace's `TG_HOST`, `TG_SECRET`, and `TG_GRAPHNAME` (must be `FraudInvestigation`; the code refuses to run against the protected `Transaction_Fraud` graph). No value is ever committed or printed by the code.

An optional LLM-orchestrated investigator can be enabled the same way via `config/llm.example.env`; it is disabled by default, and the deterministic policy layer remains authoritative regardless of whether it is on.

---

## Hackathon Context

Built for the **TigerGraph × Hacker House Goa** agentic fraud investigation challenge. The original challenge specification is preserved in full, unmodified, at [`docs/hackathon-spec.md`](docs/hackathon-spec.md).

---

## Submission

The 20 answer files are in [`cases/`](cases/), in the format specified by the challenge. Known limitations of the submission — why `fraud_probability` is left `null`, how simulated evidence responses are handled, and why no accuracy figure is claimed — are documented in [`SUBMISSION.md`](SUBMISSION.md).
