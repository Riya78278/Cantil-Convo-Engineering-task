# Phase 1 · Corvo procurement specification

Policy v5.0 applies from 1 April 2026. Values are INR excluding GST. This matrix is the acceptance specification for the future engine. The Phase 1 verifier checks the supplied kit and specification only.

| Clause | Inputs and required behavior | Acceptance case |
|---|---|---|
| 1.1 | Resolve a supplied valid item code first. Otherwise normalize the description and match catalogue specifications. | An abbreviated bearing description resolves only when the size and suffix agree. |
| 1.2 | Treat an unmatched description as NEW. Value at quantity × estimated unit price. | Two new-item lines receive separate groups, NEW_ITEM_SOURCING and no suggestions. |
| 2.1 | Apply limits to total requirement quantity and value. | Two eligible lines below the spot limit can together trigger RFQ. |
| 2.2 | Sort by creation timestamp, PR number, numeric line. Join the earliest eligible group of the same item and plant, within 14 calendar days and ±7 need-by days of its first line. | Day 14 and ±7 qualify. Day 15, ±8 or another plant start another group. Check a third line against the first, even if it is close to the second. |
| 2.3 | First line sets the requirement date. Any emergency line makes the group an emergency. | A later emergency line changes urgency while history and contract lookups use the first date. |
| 3.1 | Multiply total quantity by usable contract rate, else lowest applicable exhausted rate, else latest item PO price in prior 365 days, else standard price. | Even when no contract can fund the group, the lowest applicable rate takes priority over PO history for valuation. |
| 4.1 | Check covered item, inclusive contract dates and Active supplier. | Valid on both endpoints, invalid one day outside or with a Blocked supplier. |
| 4.2 | Start at register consumption. Allocate requirements chronologically against shared contract ceiling using quantity × that contract's rate. | Exact remaining balance qualifies. One paise over fails. Another item on that contract sees the reduced balance after a contract PO. |
| 4.3 | Choose cheapest applicable contract with sufficient ceiling. | Try the second-cheapest when the cheapest cannot fund the whole group. |
| 4.4 | Reconcile register and signed PDFs by contract ID. Post-register contracts start at zero and bind from their start date. | Import a PDF-only agreement once. Its rate becomes eligible on its start date. |
| 5.1 | For a matched Capex item, route first to CAPEX_SOURCING, require capex approval and request five suggestions. | Capex with a usable contract still goes to sourcing and consumes no ceiling. |
| 5.2 | Next, issue CONTRACT_PO at the usable contract's supplier and rate. | An emergency with a usable contract takes the contract route. |
| 5.3 | Next, Emergency plus value ≤ 500000 plus purchase from an Active supplier in prior 365 days yields EMERGENCY_PO at first preferred supplier's reference price. | At 500000 qualifies. At 500000.01 continue to later rules. |
| 5.4 | Next, value < 200000 with qualifying Active purchase history yields SPOT_PO at first preferred supplier's reference price. | For ordinary demand, 199999.99 qualifies and 200000 goes to RFQ. |
| 5.5 | Next, value < 1000000 yields RFQ, request three suppliers. | A small ordinary item without qualifying history goes to RFQ. |
| 5.6 | Remaining matched requirements valued at ≥ 1000000 yield RFP, request five suppliers. | At exactly 1000000 choose RFP if earlier rules missed. |
| 5.7 | Exclude Blocked and Inactive suppliers from award and suggestions. | Even the best-rated ineligible supplier receives neither. |
| 6.1 | Preferred candidates must be Active and have item PO history in prior 548 days or an item contract that started by the as-of date. | An expired contract qualifies for preference. A future contract alone does not. |
| 6.2 | Rank by frequency, recency, contract experience, delivery and quality. Recalculate at requirement or export date. | Show each factor and use reproducible tie-breaking. Compare all three worked rankings. |
| 6.3 | Reference price is supplier's latest item PO price within 548 days, otherwise its most recent started item contract rate. | With both records use PO price. Contract-only supplier uses contract rate. |
| 6.4 | Start sourcing suggestions with ranked preferences. Fill from other Active category suppliers ordered by delivery and quality. | Return fewer than requested if exhausted, without duplicates. NEW gets none. |
| 7.1 | Consolidate PO requirements only for same supplier, plant and route within 24 elapsed hours of first requirement timestamp. | Exactly 24 hours qualifies, 24 hours and one minute does not. Test same supplier with differing routes. |
| 7.2 | In creation order join earliest qualifying open PO or start a new one. Never combine sourcing requests. | Two distinct requirements can share a PO ID while retaining separate group IDs and group values. |
| 8.1 | Preserve original decision, clause, policy version and supporting contract or PO. Record actor, time and written override reason. | Reject empty override comments. After override, original reasoning remains available. |

## Application acceptance after Phase 1

- Requester chat collects item, quantity, plant, need-by and urgency, resolves ambiguous text with the requester, requests an estimate for a new item and confirms before creation.
- CSV upload and POST /api/process-batch use the same engine as chat. Buyer view shows constituent lines, value, supplier and rate or suggestions, clause, version and source. Approval creates the corresponding PO or sourcing event. Repeated approval must not create another.
- Procurement can supply a policy document or describe an amendment in chat. Parse into a dated draft with ordered conditions and outcomes. Preview plain-language changes, allow correction, then activate on approval. Test inserting a plant/category exception, changing a limit, window and rule order, and replay before/on the effective date.
- Buyer can add a PDF-only contract. Preferred rankings can be exported as of a chosen date. Store approvals, overrides and version history in a persistent database.
- Verify a one-command local run and a hosted repeat run. README covers setup, APPROACH.md records tools and architecture, DECISIONS.md explains ambiguity. Keep source data private. Freeze by Thu 19 Nov 2026 10 AM IST. By noon IST push only the three named outputs to submission/.

## Private kit audit and rehearsal

The sample has 73 items, 48 suppliers, two plants, 468 historical PO rows, 10 register entries, 26 register lines and 12 signed copies. Two agreements exist only as signed copies. Use the actual files as input. Among 186 PR lines, 39 omit the item code, one has zero quantity and one references an unknown plant. Never encode these counts as production limits.

Worked decisions 1–4 cover contract precedence, PDF-only terms, depleted ceiling and competing contracts. Examples 5–8 cover clubbing, separate plants, abbreviation matching and emergency. Examples 9–14 cover RFQ by value, RFQ without active history, RFP, Capex, new items and consolidated POs. Compare all after engine implementation. Check underlying membership and actual decimal rates, since the examples round prices and sometimes show a single representative value for multiple requirements. No full sample answer key is supplied.

Run the Phase 1 checks from the project root with Python 3:
```sh
python3 -B tests/check_phase_one.py "/path/to/Cantill_FullStack_Task_Kit_vf(1).zip"
```
The verifier reads the original ZIP in memory. No package installation or extraction is required.
