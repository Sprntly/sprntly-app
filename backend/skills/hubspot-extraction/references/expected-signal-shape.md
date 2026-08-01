# HubSpot extraction — expected signal shape

Every signal this skill emits still fills the caller's fixed extraction
schema (`kind`, `content`, `source_type`, `theme`, `relationship`,
`properties`, `confidence` — see `app.graph.extractor._EXTRACT_SCHEMA`).
This doc pins the VALUES this skill is expected to choose, by HubSpot record
kind, so its output can be checked structurally against a declared contract
rather than only against free-text prose.

| HubSpot record kind | source_type     | typical `kind` values                          | properties carried through            |
|----------------------|------------------|--------------------------------------------------|-----------------------------------------|
| `deal`               | revenue          | deal_blocker, finding                            | amount_usd, stage, pipeline             |
| `ticket`             | customer_voice   | bug, feature_request, incident, finding          | priority, category                      |
| `note`               | customer_voice   | sentiment, feature_request, finding              | owner_id                                |
| `email`              | customer_voice   | sentiment, feature_request, finding              | direction                               |
| `line_item`          | revenue          | finding                                          | sku, quantity, amount_usd               |
| `owner`              | (never extracted) | —                                               | —                                        |

## Worked examples

**deal → deal_blocker**

Input:
```
[hubspot/deal id=901 at=2026-07-01]
title: Acme Robotics — Q3 renewal
data: amount_usd=42000, stage=closedlost, pipeline=Enterprise
Lost to a competitor after a 3-week delay on a custom export API; buyer
cited the missing CSV export as the deciding factor.
```
Output:
```json
{
  "kind": "deal_blocker",
  "content": "Acme Robotics ($42,000) lost to a competitor after the buyer cited missing CSV export as the deciding factor",
  "source_type": "revenue",
  "theme": "CSV export",
  "relationship": "PRESSURES",
  "properties": {"amount_usd": 42000, "stage": "closedlost"},
  "confidence": 0.9
}
```

**ticket → feature_request**

Input:
```
[hubspot/ticket id=4471]
title: Can we get CSV export?
data: priority=medium, category=product feedback
Customer asked twice this month for a way to export their dashboard as CSV.
```
Output:
```json
{
  "kind": "feature_request",
  "content": "Customer asked twice for CSV export of their dashboard",
  "source_type": "customer_voice",
  "theme": "CSV export",
  "relationship": "REQUESTS",
  "properties": {"priority": "medium"},
  "confidence": 0.85
}
```

**note → sentiment**

Input:
```
[hubspot/note id=8820]
title: CRM note
data: owner_id=1029
Customer said the onboarding flow "finally clicked" after the new tooltip
shipped — much less confused than the last call.
```
Output:
```json
{
  "kind": "sentiment",
  "content": "Customer reported the onboarding flow finally clicked after the new tooltip shipped, much less confused than the prior call",
  "source_type": "customer_voice",
  "theme": "Onboarding clarity",
  "relationship": "SUPPORTS",
  "properties": {"owner_id": "1029"},
  "confidence": 0.75
}
```

**owner → no signal**

Input:
```
[hubspot/owner id=1029]
title: Priya Shah
data: email=priya@acme.com
```
Output: no signal extracted — owner records are attribution only.
