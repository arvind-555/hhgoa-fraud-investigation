# R7 assessment: "disputed but legitimate" (recurring pattern) — not evaluable

**Rule R7 (Fraud Policy v1.0):** a customer disputes a charge that matches their own recurring pattern (same merchant, same amount, monthly) -> CREATE_CASE, VERIFY_WITH_CUSTOMER, WARN_CUSTOMER, do not block.

**Finding:** the dataset has **no merchant field** (only ProductCD, billing region, email domains and anonymised C/D/V features), so R7 cannot be evaluated as written. This was tested, not assumed:
`scripts/analysis/08_r7_assessment.py` (output `scripts/analysis/output/08_r7_assessment.json`) measures a merchant-free proxy — same card + same ProductCD + identical amount, recurring 27-34 days
earlier, point-in-time (earlier transactions only) — on the 5,407 closed cases known at 2016-11-01.

| Proxy | Confirmed-fraud cases | Cleared cases | Reading |
|---|---|---|---|
| monthly repeat (27-34 d) | 111 / 4,513 = 2.5% [2.1, 3.0] | 6 / 894 = 0.7% [0.3, 1.5] | **wrong direction**: fraud cases repeat more than cleared; only 6 cleared hits (needs >= 30) |
| identical amount earlier (any gap) | 599 / 4,513 = 13.3% | 55 / 894 = 6.2% | wrong direction again |

Gate for turning the proxy into a signal: >= 30 hits in both classes, non-overlapping intervals with cleared higher than fraud, out-of-time reproduction. **It fails all three.** A recurring-amount proxy would
push the agent toward "legitimate" exactly where the history shows more fraud, so it is NOT used.

**Decision (implemented):**
* R7 stays **not evaluable**: every case lists "no merchant field: policy R7 (recurring merchant) cannot be evaluated" under investigation uncertainty (missing information).
* No action cites R7 and no signal is derived from recurrence. `retrieve_policy` still returns the R7 text so an explanation can quote it.
* A customer dispute (trigger `customer_report`) is still handled by the other rules: a case is opened (policy 3a), the customer is asked to validate, and the simulated response decides R2/R3.
* Revisit only if a merchant (or an equivalent recurring-payment identifier) becomes available; then the proxy gate above must be re-run.
