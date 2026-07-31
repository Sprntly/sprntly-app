# Jira extraction — expected signal shape

Every signal this skill emits still fills the caller's fixed extraction
schema (`kind`, `content`, `source_type`, `theme`, `relationship`,
`properties`, `confidence`). This doc pins the `type` → `kind` mapping so
output can be checked structurally.

| Jira native `type`         | `kind`                                    | `source_type`   |
|-----------------------------|--------------------------------------------|-------------------|
| Bug                         | bug                                         | project_mgmt      |
| Story / Task (user-facing)  | feature_request                             | project_mgmt      |
| Story / Task (internal)     | finding                                     | project_mgmt      |
| Epic                        | finding                                     | project_mgmt      |
| other / custom              | feature_request or finding (content-derived)| project_mgmt      |

## Worked example

Input:
```
[jira/issue id=PROJ-142 at=2026-07-10]
title: Users can't export dashboard as CSV
data: status=In Progress, priority=High, type=Bug, project=Platform, labels=[export, dashboard]
CSV export button throws a 500 error for any dashboard with more than 3 widgets.
```
Output:
```json
{
  "kind": "bug",
  "content": "CSV export button throws a 500 error for dashboards with more than 3 widgets",
  "source_type": "project_mgmt",
  "theme": "CSV export",
  "relationship": "AFFECTS",
  "properties": {"status": "In Progress", "priority": "High", "issue_type": "Bug"},
  "confidence": 0.9
}
```

## Worked example — Story, user-facing

Input:
```
[jira/issue id=PROJ-201 at=2026-07-11]
title: Allow bulk CSV export of multiple dashboards
data: status=To Do, priority=Medium, type=Story, project=Platform, labels=[export]
Users have asked to select several dashboards and export them all as one CSV batch.
```
Output:
```json
{
  "kind": "feature_request",
  "content": "Users asked to select several dashboards and export them all as one CSV batch",
  "source_type": "project_mgmt",
  "theme": "CSV export",
  "relationship": "REQUESTS",
  "properties": {"status": "To Do", "priority": "Medium", "issue_type": "Story"},
  "confidence": 0.8
}
```

## Worked example — Bug with sales/revenue framing in the description

The description quotes sales/CSM language ("won't renew", "deal-critical
blocker"), but `type` is still `Bug` — `kind` stays `"bug"`, never
`"deal_blocker"` (that value belongs to `hubspot-extraction`'s vocabulary,
not this skill's). The revenue stakes ride into `properties` on the SAME
signal instead of producing a second, separately-framed one.

Input:
```
[jira/issue id=ENT-4821 at=2026-07-20]
title: Enterprise customer can't renew until SSO group sync ships
data: status=In Progress, priority=Highest, type=Bug, project=Platform, labels=[sso, enterprise]
Acme Corp (our largest enterprise account, $180k ARR) has told their CSM they
will not renew their contract next month unless SSO group sync is delivered.
Their security team blocked the renewal pending this. Sales has flagged this
as a deal-critical blocker for the quarter.
```
Output — ONE signal, `kind` still within `{bug, feature_request, finding}`:
```json
{
  "kind": "bug",
  "content": "Acme Corp ($180k ARR) will not renew their contract next month unless SSO group sync is delivered; their security team has blocked the renewal pending this",
  "source_type": "project_mgmt",
  "theme": "SSO group sync",
  "relationship": "AFFECTS",
  "properties": {"status": "In Progress", "priority": "Highest", "issue_type": "Bug", "customer": "Acme Corp", "revenue_at_risk_usd": 180000, "blocks_renewal": true},
  "confidence": 0.9
}
```

## Worked example — Epic

Input:
```
[jira/issue id=PROJ-190 at=2026-06-30]
title: Export & reporting overhaul
data: status=In Progress, priority=Medium, type=Epic, project=Platform, labels=[export, reporting]
Umbrella epic tracking every export-related workstream this quarter.
```
Output:
```json
{
  "kind": "finding",
  "content": "Export & reporting overhaul is an active epic tracking every export-related workstream this quarter",
  "source_type": "project_mgmt",
  "theme": "CSV export",
  "relationship": "RELATES_TO",
  "properties": {"status": "In Progress", "priority": "Medium", "issue_type": "Epic"},
  "confidence": 0.7
}
```
