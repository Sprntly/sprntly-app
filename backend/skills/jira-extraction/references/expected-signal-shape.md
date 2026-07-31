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
