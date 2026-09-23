# From Fraud Alert to Defensible Action: Building an Agentic Fraud Investigation System with TigerGraph

*How we combined a temporal knowledge graph, GraphRAG, agentic investigation, and deterministic policy controls to turn suspicious transactions into auditable next-best actions.*

---

Every fraud team already has a way to generate alerts — a risk model scores a transaction, a rule fires, a customer calls in. That part is not the hard problem anymore.

The hard problem starts one step later: given an alert, **what do you actually look at, when do you have enough to act, what do you do if you don't, and what are you allowed to do once you decide?** Those four questions are what our system for the TigerGraph × Hacker House Goa fraud investigation challenge is built around.

This post covers the architecture, temporal safety, GraphRAG, the deterministic decision authority, a real benchmark case (HHG-014), and what an actual LLM-orchestration experiment told us about where a model does and doesn't belong here. Every number below comes from the repository, not from memory.

- **Live demo:** https://hhgoa-fraud-investigation.onrender.com
- **GitHub:** https://github.com/arvind-555/hhgoa-fraud-investigation

## 1. Fraud investigation is not classification

The challenge dataset (IEEE-CIS, anonymized, 590,742 transactions with a risk score in place of a label) makes the temptation obvious: train a classifier, threshold the score, done — not what the challenge asks for, and not what a fraud analyst does.

An analyst who opens a flagged transaction doesn't output `fraud: 0.83`. They ask: *is this card connected to anything else I should worry about? Do I have enough to act, or do I need to check with the customer first?* The output isn't a probability — it's a decision about what happens next, and who has to sign off on it.

Our system answers that question, not the classification one: it investigates the transaction's neighborhood, gathers evidence, decides whether that evidence is sufficient, and — only under fixed policy — recommends an action with an approval route.

## 2. Why relationships matter

A single transaction row tells you an amount, a channel, a merchant category. It does not tell you that the device behind it was used by eleven other customers last month, or that its device profile matches one that funded a confirmed-fraud case three months ago.

Those facts only exist as *relationships*: Customer owns Card, Card made Transaction, Transaction came from Device, Device seen on other Cards, other Cards belong to other Customers. Fraud rings are almost never visible in one row — they're visible in the *shape* of the graph around it.

## 3. Why TigerGraph is central

We built one graph, `FraudInvestigation`, on TigerGraph 4.2.5 (Savanna), separate from the pre-existing `Transaction_Fraud` graph — the code refuses to run against anything else by construction (the graph name is checked and hard-fails otherwise).

The schema has 9 core vertex types and 18 relationship types (each with an automatic reverse edge):

```
CREATE VERTEX: Customer, Card, Transaction, DeviceProfile,
               EmailDomain, BillingRegion, ClosedCase,
               FI_Case, TextChunk

CREATE EDGE:   OWNS / OWNED_BY            Customer  -> Card
               MADE / MADE_BY             Card      -> Transaction
               NEXT / PREV                Transaction -> Transaction
               FROM_DEVICE / DEVICE_OF    Transaction -> DeviceProfile
               SEEN_ON / SEEN_BY          DeviceProfile -> Card
               PURCHASER_EMAIL, RECIPIENT_EMAIL       -> EmailDomain
               BILLED_IN                 Transaction -> BillingRegion
               CLOSED_ON_CARD, CLOSED_ON_CUSTOMER,
               CLOSED_INVOLVES, CLOSED_CONNECTED_TO   ClosedCase -> ...
               CASE_ON_CARD, CASE_TXN, CASE_CONNECTED_TO,
               CASE_CITES_DEVICE, SIMILAR_CASE        FI_Case -> ...
               DESCRIBES                 TextChunk -> ClosedCase / FI_Case
```

To be precise about the division of labor: **TigerGraph stores the connected facts and answers traversal queries. It does not reason about fraud.** Every query (`fi_shared_devices`, `fi_connected_entities`, `fi_card_history`, `fi_prior_cases`, `fi_txn_context`, `fi_customer_history`, `fi_text_chunks`) returns raw, time-bounded neighborhoods and counts. Interpreting them happens entirely in application code (`src/fraud_tools/`, `src/agent/`). The graph answers "what's connected to what, as of when"; it doesn't decide anything.

One detail worth calling out: `fi_shared_devices` **hub-gates** by design. A device profile shared by an implausible number of customers (a null/default fingerprint, say) is never expanded — it's reported separately as a `skipped_hub`, so one ubiquitous device can't pull half the customer base into every investigation.

## 4. System architecture

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

| Layer | Responsibility |
|---|---|
| **TigerGraph** | Connected entity storage; answers whitelisted, time-bounded traversal queries. No fraud reasoning. |
| **GraphRAG** | Retrieves prior closed cases, fraud-pattern text, and policy rules as context — text retrieval, not traversal. |
| **Investigation Agent** | Orchestrates tool calls, synthesizes evidence, assesses whether it's sufficient. |
| **Policy Authority** | A fixed decision matrix — evidence in, verdict/action/route out. No model in this layer. |
| **Case Record** | The auditable output: an `FI_Case` graph vertex plus the full decision trail. |

## 5. Temporal safety: the `as_of` design

A benchmark case is scored on what an investigator could have known *at the moment it opened* — not what happened afterward, and not which closed cases were labeled after the fact. If the agent could see the future, the evaluation would be meaningless.

Every graph query in this system takes a required `INT as_of` parameter with **no default value**, and filters on edge `epoch`, never on a stored "current" flag:

```gsql
CREATE OR REPLACE QUERY fi_shared_devices(
    VERTEX<Card> card, INT as_of, INT days, INT max_customers
) FOR GRAPH FraudInvestigation { ... }
```

Three rules enforce this:

- **Transactions and edges** are only traversed if their `epoch <= as_of`.
- **Closed cases** only become visible once `close_epoch <= as_of` — a case that closed after the investigation started doesn't exist yet, as far as the agent is concerned.
- **GraphRAG text chunks** carry a `valid_from_epoch`; a closed-case narrative chunk isn't retrievable until its case has actually closed.

`as_of` is fixed by the trusted runner from the case's own `opened_at` timestamp before the agent starts, and never appears in the agent's own request surface — the tool-call schema simply has no `as_of` parameter, and it's one of 24 argument names the permission layer refuses by name if anything tries to pass it. This isn't a policy the agent is asked to respect; it's a parameter it cannot supply.

The result the repository actually measures: **0 temporal-leakage events** in the LLM-orchestrated evaluation runs, explicitly counted every run, plus a backend suite (434 tests) including multiple tests asserting the temporal guard actually raises when future data is attempted — not "we believe leakage doesn't happen," but a check that runs and is enforced.

## 6. GraphRAG and case memory

It's worth being precise about what "GraphRAG" means here, since it's easy to conflate with graph traversal:

- **TigerGraph traversal** answers structural questions: which cards share this device, what did this card do in the last 48 hours, is it connected to a prior case.
- **GraphRAG** answers contextual questions: what do similar closed cases look like, what does policy say, what known pattern does this match.

The knowledge base for GraphRAG is exactly four document types — `closed_case`, `policy`, `pattern`, `format` — stored as `TextChunk` vertices with `valid_from_epoch`, retrieved via lexical BM25 (no vector embeddings; retrieval is deterministic lexical matching over the same temporally-filtered chunks). Closed-case chunks are templated (`"<pattern> | <outcome> | <channel> | exposure <band>: <analyst notes>"`) — they give wording and base-rate context, never a label for the case under investigation, and a hard assertion (`assert_clean`) fails the chunk build if any chunk ever mentions a benchmark case ID.

So GraphRAG is *contextual memory and text retrieval* — it never traverses the entity graph, and it's never the verdict source; that's the policy layer's job.

## 7. The agentic investigation loop

This is what makes the system agentic rather than a fixed pipeline: tool-call count and choice aren't hardcoded per case. The orchestrator (`src/agent/orchestrator.py`) moves through a fixed state machine, but *what happens inside each state* depends on the evidence:

```
TRIGGER
  → INITIAL_INVESTIGATION        (tool selection, graph traversal)
  → EVIDENCE_SYNTHESIS           (combine everything returned)
  → UNCERTAINTY_ASSESSMENT       (is this evidence sufficient?)
  → [ADDITIONAL_EVIDENCE_REQUEST]  (only if policy says it's needed)
  → NEXT_BEST_ACTION              (deterministic decision matrix)
  → EXPLANATION
  → CASE_WRITE
```

Ten whitelisted, read-only tools are available: `get_transaction_context`, `get_customer_history`, `get_card_history`, `find_shared_devices`, `find_connected_entities`, `find_prior_cases`, `detect_fraud_patterns`, `get_policy_context`, `find_similar_cases`, `retrieve_policy`. If a shared-device search turns up connected cards, the agent pulls each one's history — a case with 19 connected cards genuinely runs more tool calls than a case with none. That's the "agentic" part: **the investigation's shape follows the evidence, not a fixed script.**

What's deterministic versus LLM-assisted matters for the rest of this post: **tool selection, order, and stopping can optionally be delegated to an LLM investigator.** Evidence synthesis, uncertainty assessment, the decision matrix, approval routing, and the case write never are — fixed Python logic, the same for every run.

## 8. Controlled additional evidence and uncertainty

Uncertainty assessment produces an `evidence_strength` (`none` / `weak` / `moderate` / `strong`), an independent-source count, and a list of what's still missing — never a guessed answer. If that's insufficient under policy, the agent may request customer verification or step-up authentication.

That request is deterministically gated. The model — if one is orchestrating — can only *suggest* one; a separate, non-LLM check decides `NOT_SUGGESTED` / `ALLOWED_POLICY_REQUIRED` / `REJECTED_NOT_REQUIRED` before anything is issued. A response comes from a deterministic simulator, never an actual customer or a model roleplaying one — the benchmark supplies no real responses, so the simulator's one built-in assumption is "no reply within 24 hours," treated as *absence of evidence*, never confirmation or denial. Every simulated value is tagged `[SIMULATED]` and marked `simulated: true`, so it's never confused with graph-derived evidence.

## 9. Deterministic policy authority and approval routes

This layer actually decides what happens, and it is intentionally boring: a fixed decision matrix mapping five evidence classes to actions and routes.

| Class | Evidence at `as_of` | Typical initial actions |
|---|---|---|
| **A** | Strong graph evidence (device ring, ≥2 independent validated sources) | `CREATE_CASE`, `ESCALATE_TO_ANALYST`, `FILE_REPORT`, `MONITOR_CONNECTED_CARDS` |
| **T+** | Customer report (immutable) + one validated source | `BLOCK_CARD`, `CREATE_CASE`, conditional `FILE_REPORT` |
| **T** | Customer report only, weak/no graph evidence | `VERIFY_WITH_CUSTOMER`, `MONITOR_CARD`, `CREATE_CASE` |
| **B** | One validated independent source | `STEP_UP_AUTH` / `VERIFY_WITH_CUSTOMER`, `MONITOR_CARD` |
| **C** | Weak context only | `STEP_UP_AUTH` / `VERIFY_WITH_CUSTOMER`, or `ALLOW_TRANSACTION` if there's genuinely nothing |

Every action carries an approval route — `auto`, `L1` (team lead), or `L2` (fraud manager) — decided by fixed thresholds (`BLOCK_CARD` routes L1 under a fixed exposure ceiling, L2 above it). **A customer's own denial is immutable**: once reported, no simulated reply can reverse it. The verdict follows directly from the class and post-verification outcome — strong evidence or a denied report is `fraud`; confirmed/passed verification alone is `legitimate`; anything short of the bar is `uncertain`, never a guess.

Graph writes are equally constrained: only `FI_Case` and its own outgoing edges can be written, content-hashed and idempotent, with source facts — `Transaction`, `Customer`, `Card`, `DeviceProfile`, `ClosedCase` — structurally unwritable from this path. Nothing recommended is ever executed; every action carries `executed: false`.

## 10. Walkthrough: HHG-014

HHG-014's trigger is an analyst request, not a model score or a customer complaint:

> *"Analyst request: several cards this month show purchases from the same unusual device profile. Review transaction 3478561 on card C13487-K1 and look for related activity."*

**Initial investigation.** The agent pulls transaction context, customer history, card history, prior cases, and pattern detection — nine distinct tool types in its first pass. `detect_fraud_patterns` fires four signals: **S01** (shared-origin device ring — a compound anomaly), **S10** (new device ID), **S11** (anonymizing proxy), **S12** (new device *and* proxy together).

**Graph expansion.** `find_connected_entities` returns 19 other cards sharing the flagged device. The agent then pulls **each of those 19 cards' own history** — one `get_card_history` call per connected card, because the shared-device signal alone isn't evidence about what those cards actually did; the graph has to show it. That expansion finds 24 transactions across those 19 cards inside the same 30-day window. In total: **29 tool calls, 28 graph queries, 10 served from cache**, 46 seconds end to end.

**Contextual evidence.** `find_similar_cases` (GraphRAG) returns 10 similar closed cases by text similarity — zero directly linked to this card, customer, or device; precedent, not proof. `retrieve_policy` pulls the actual text of R1, R4, R6, and R9 in as citable evidence.

**Uncertainty.** Evidence strength comes out **strong** (the S01 ring plus the pattern-defining signals), but `needs_more_evidence` is still `True` — verification is missing and the model alert band is only "low." Recorded as missing: no merchant field (so R7 can't be checked), and no verification reply yet.

**Additional evidence.** The agent requests `customer_validation`. No real reply exists for this benchmark, so the simulator returns the one thing it's allowed to: *"[SIMULATED] No reply received within 24 hours"* — tagged simulated, not treated as a denial or confirmation.

**Policy evaluation and final action.** Under the no-reply rule (R4), the initial action set (`CREATE_CASE`, `ESCALATE_TO_ANALYST`, `FILE_REPORT[L2]`, `MONITOR_CONNECTED_CARDS`, `VERIFY_WITH_CUSTOMER`) gains two more: `MONITOR_CARD` and `DECLINE_TRANSACTION[L1]`. **Verdict: fraud. Status: escalated.** A suspicious-activity report is recommended, citing R9 (coordinated activity across customers fitting no documented pattern) and R6 (shared device profile), generated from case facts, not boilerplate.

**Case record.** The case writes to the graph as `CASE-HHG-014`, revision 3, content-hashed and idempotent — unchanged content writes nothing; changed content bumps the revision. Verified directly: running this same investigation live against TigerGraph today reproduces a content hash matching the stored revision-3 vertex exactly, with no additional write.

One honest caveat, from our own second-pass audit, not hidden: the S01 ring rests on one seeded device-sharing signal without an independently labeled precedent, and all 26 nearby transactions are treated as one episode on device-sharing alone — roughly 81% of the early burst was never itself part of a closed case. It's the single case in our 20-case adversarial re-review rated a residual **MEDIUM** concern rather than LOW; the rest of the chain checks out, but the ring evidence's strength is disclosed as imperfect, not overstated.

## 11. LLM orchestration: what we actually measured

We ran a real LLM-orchestrated version of the investigator (`HHG_LLM_PROVIDER=openai`, GPT-5 mini), where the model chose which tools to call, in what order, and when to stop — with the deterministic evidence rules, decision matrix, and policy layer left untouched underneath it.

**First run, 6 of 20 cases, stopped by design.** The harness halts immediately on any safety violation, and it did: on HHG-006, the model requested customer verification even though the deterministic layer had already determined none was needed, and that request changed the final action set — a real policy-drift violation, caught exactly as intended.

We fixed the two integration issues this exposed — equivalent tool calls weren't being recognized as duplicates, and the model's evidence *suggestions* weren't gated by policy before executing — and re-ran the same six cases. Safety was clean across all three attempts: **0 temporal leakage, 0 forbidden-parameter attempts, 0 benchmark IDs from other cases, 0 executed actions, 0 fabricated evidence.** The honest finding didn't change, though:

- **No case showed the LLM discovering evidence the deterministic pipeline missed.**
- Deterministic and LLM-orchestrated authority (verdict, final actions, SAR) was **identical in every one of the five measured cases** once policy drift was fixed — HHG-004 was attempted but never measured after three attempts all hit TigerGraph read timeouts.
- The LLM path cost 5,000–17,000 tokens per case and was not faster in any comparable run — about 3× the deterministic pipeline's latency in the initial, clean run; the post-fix latency numbers were not comparable because TigerGraph was unstable during that re-run.

So the engineering decision isn't "LLMs don't work here." It's: **use the LLM where it can genuinely help, and don't give it authority that a deterministic system already enforces more reliably.** The deterministic pipeline produced the 20 submitted answer files (`tokens: 0` in every one). The LLM investigator stays in the codebase, opt-in, sandboxed, and safety-gated — not the default path.

## 12. Engineering validation and reproducibility

No answer key was ever provided for the 20 benchmark cases, so **we make no accuracy claim** — no precision, recall, F1, or "N/20 correct." What follows is checkable by re-running the repository's own tests.

| Check | Result |
|---|---|
| Benchmark cases processed end to end | 20 / 20 |
| `FI_Case` graph records written and validated | 20 / 20 |
| SAR records validated for internal consistency | 20 / 20 |
| Temporal-leakage violations | 0 |
| Policy violations | 0 |
| Graph-invariant violations | 0 |
| Backend test suite | 434 tests passing |
| Frontend test suite | 33 tests passing |
| Independent evidence-strength oracle vs. live agent (40 sampled non-benchmark transactions) | 40 / 40 agreement |
| Adversarial re-audit of all 20 submitted cases | 0 HIGH findings, 1 MEDIUM, 19 LOW |
| Deterministic reproducibility | re-running produces byte-identical case content and graph-write hashes |

On `fraud_probability`: it's `null` in every one of the 20 submitted files, deliberately. The only labeled outcomes available are the 5,565 closed investigations, and they're not representative of the alert population — about 84% of closed cases are confirmed fraud, while the specification itself states roughly half the benchmark cases are legitimate. Calibrating from a differently-shaped population would just transfer its base rate onto this one, so we built the calibration gates anyway (`src/calibration/`, `config/calibration_v1.json`) and let them fail honestly rather than loosen them or substitute a placeholder — also why our own verdicts skew toward `uncertain` rather than `legitimate`: without a real reply confirming innocence, the system won't guess `legitimate` just because a case looks routine. Policy thresholds phrased as probabilities in the specification (0.30 for case-opening, 0.85/0.15 for stopping) are instead applied through evidence-strength classes and independent-source counts, which the data actually supports.

## 13. What we learned

**Separating agentic from consequential was the single most load-bearing design decision.** Variable tool calls, variable depth, an optional LLM — none of it touches the part that carries consequences, and keeping that boundary hard rather than conventional is what makes the rest of this trustworthy.

**Temporal correctness has to be structural.** A required `as_of` parameter with no default, checked at the query layer, is worth more than any amount of "the agent is instructed not to look at future data."

**An honest negative result is still a result.** The LLM experiment could have been quietly dropped instead of documented down to the safety violation it triggered — "we tested it and it didn't help" is exactly the finding a production fraud team needs, not a footnote.

**Ring evidence from one signal source is real but thin.** HHG-014's S01 ring is the most dramatic case in the set, and also the one our own adversarial review flagged as resting on uncorroborated evidence. Better to say that plainly than let the most impressive-looking case be the least scrutinized.

## 14. What remains imperfect

- `fraud_probability` is null everywhere; if the scoring path strictly requires a numeric value, this field won't score, and we chose that over inventing one.
- Ring/shared-device evidence (S01) relies on a single traversal signal without a second, independent confirming source for the connected cards it implicates.
- Customer, step-up, and analyst responses are simulated for every case (there's no real customer to answer), which caps how many cases reach a confident `legitimate` verdict — disclosed honestly rather than fabricated.
- The LLM-orchestrated path is measured on 6 of 20 cases (5 fully, per §11); we didn't spend further budget once deterministic and LLM authority were established as identical.
- No official accuracy figure exists, and none is claimed.

## What we built

An agent that, given a fraud alert, investigates a connected graph of customers, cards, transactions, and devices at a fixed point in time; retrieves prior cases and policy text through GraphRAG; assesses whether its evidence is sufficient and requests more only under policy control; and produces a next-best action with an approval route — through a decision layer a language model is never allowed to override. A read-only live-investigation mode on the deployed demo runs this same pipeline against live TigerGraph on request, with no graph write, so it's inspectable in real time, not only as a replay.

Built for the **TigerGraph × Hacker House Goa** challenge.

- **Live demo (including the live investigation preview):** https://hhgoa-fraud-investigation.onrender.com
- **GitHub repository:** https://github.com/arvind-555/hhgoa-fraud-investigation
