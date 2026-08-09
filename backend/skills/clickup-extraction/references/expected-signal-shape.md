# ClickUp extraction — expected signal shape

ClickUp supplies no native issue type (see `app.kg_ingest.pullers.clickup`),
so `kind` here is inferred, not read off a field — this doc pins the
inference rules so output can be checked structurally.

| Signal (title/description/list/tags)                                  | `kind`           | `source_type`  |
|---------------------------------------------------------------------------|--------------------|-------------------|
| states broken/incorrect behavior, or list/tag is bug-flavored             | bug                | project_mgmt      |
| states new/changed capability, or list/tag is feature-planning-flavored   | feature_request    | project_mgmt      |
| neither — routine/internal work                                           | finding            | project_mgmt      |

## Worked example — clear bug (quoting a customer, still project_mgmt)

Input:
```
[clickup/task id=88213 at=2026-07-12]
title: Dashboard export is broken for large workspaces
data: status=open, priority=urgent, list=Bugs, tags=[export, p1]
Reported by a customer via support: exporting a dashboard with 50+ widgets
times out instead of downloading.
```
Output:
```json
{
  "kind": "bug",
  "content": "Dashboard export times out for workspaces with 50+ widgets instead of downloading, reported by a customer via support",
  "source_type": "project_mgmt",
  "theme": "Dashboard export",
  "relationship": "AFFECTS",
  "properties": {"status": "open", "priority": "urgent", "list": "Bugs"},
  "confidence": 0.85
}
```

## Worked example — feature request, no bug wording

Input:
```
[clickup/task id=90144 at=2026-07-13]
title: Allow scheduled exports
data: status=to do, priority=normal, list=Feature Requests, tags=[export]
Add a way to schedule a recurring CSV export instead of triggering it manually every time.
```
Output:
```json
{
  "kind": "feature_request",
  "content": "Add a way to schedule a recurring CSV export instead of triggering it manually every time",
  "source_type": "project_mgmt",
  "theme": "CSV export",
  "relationship": "REQUESTS",
  "properties": {"status": "to do", "priority": "normal", "list": "Feature Requests"},
  "confidence": 0.75
}
```

## Worked example — no signal (bare, unclear task)

Input:
```
[clickup/task id=90200 at=2026-07-13]
title: Follow up
data: status=open, priority=null, list=Admin, tags=[]
```
Output: no signal extracted — no concrete claim about anything broken or
wanted; a bare task name is not evidence.
