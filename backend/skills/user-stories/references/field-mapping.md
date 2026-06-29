# Ticket field mapping — canonical → Jira / Linear / Asana / Monday

The skill builds one **canonical ticket** and maps it to each tool's real fields at sync time.
Field schemas below are from each tool's current API/docs (researched 2026-06). Anything a tool
doesn't natively have is mapped to the closest field and flagged, never dropped silently.

## Canonical ticket (the skill's internal model)
`key` · `title` · `description` · `acceptance_criteria[]` (Given/When/Then) · `type/category`
(Product/Analytics/Reliability/CS/Localization/…) · `priority` (P0–P3) · `status` · `assignee`
· `points` · `labels[]` · `parent` (epic/feature) · `sprint_or_cycle` · `dependencies[]` ·
`provenance` (PRD §, spec task id, evidence) · `attachments[]` (PRD/prototype/evidence) ·
`route` (agent-ready / needs-human) · `traceability` (task → R# → test → PRD goal).

## Mapping table
| Canonical | Jira | Linear | Asana | Monday.com |
|---|---|---|---|---|
| title | `summary` | `title` | `name` | item `name` |
| description | `description` (ADF) | `description` (markdown) | `notes` / `html_notes` | Long Text column |
| acceptance_criteria | description section or checklist add-on | description section | description / subtask checklist | Long Text or Checklist column |
| type / category | `issuetype` (Story/Task/Bug/Sub-task) | `label` or team | `custom_field` (enum) or section | Status/Dropdown column |
| priority (P0–P3) | `priority` | `priority` 1=Urgent 2=High 3=Med 4=Low | `custom_field` (enum) | Priority/Status column |
| status | `status` (workflow) | `state` | section or enum custom field | Status column |
| assignee | `assignee` | `assignee` | `assignee` | Person column |
| points | **Story Points** (custom field) | `estimate` (XS1 S2 M3 L5 XL8) | Number `custom_field` | Numbers column |
| labels / tags | `labels` | `labels` | `tags` | Dropdown column |
| parent (epic/feature) | `parent` (Epic Link **deprecated** → use parent) | `parent` / `project` | `parent` / `projects` | Connect Boards / Subitems |
| sprint / cycle | `sprint` (Agile) | `cycle` | *(no native — use section/project)* | *(no native — use group)* |
| dependencies | issue links: **blocks / is blocked by** | issue relations | `dependencies` / `dependents` | Connect Boards or Dependency column |
| provenance (PRD §, task) | link in description or Web-link | link in description | link in description / attachment | Link column |
| attachments | attachment / remote link | attachment / link | attachment / link | File or Link column |
| due date | `duedate` | `dueDate` | `due_on` / `due_at` | Date column |

## Sync notes per tool (gotchas the skill must honor)
- **Jira:** description is **ADF (Atlassian Document Format)**, not markdown — convert. Story Points is a **custom field** whose id varies per site (resolve by name `Story Points` via `expand=names`). Epic Link is deprecating; set **parent**. Priority/issuetype/status names must match the project's scheme.
- **Linear:** GraphQL `issueCreate`; priority is an **int 0–4** (0 none, 1 urgent…4 low); `estimate` is points; team UUID required; labels by id.
- **Asana:** priority and points are **custom fields** (resolve their gids per project first); enum values are set by **option gid**, not text; section = `memberships`; dependencies set via the dependencies endpoint.
- **Monday.com:** GraphQL `create_item` + `change_multiple_column_values`; status/priority set reliably by **`index`** not label text; points → Numbers column; dependencies → Connect Boards. API version 2026-01+.

## Generic adapter — discover & match ANY tool
The four tools above are pre-mapped. For anything else (or to adapt to a team's *customized* instance of the four), the skill discovers and infers rather than assuming:

1. **Introspect:** enumerate every field/column + its type (text, single/multi-select, user, number, date, relation, rich-text).
2. **Infer role by name + type → canonical, with confidence:**
   - single-select w/ P0/High/Urgent/Low values → **priority**
   - number named points / estimate / SP / story points → **points**
   - user / people field → **assignee**
   - workflow / status field → **status**
   - long rich-text → **description**
   - relation / link / connect field → **parent / dependencies**
   - date field → **due date**
   - select w/ team-area-ish values → **category / labels**
3. **Auto-resolve on the backend & persist:** map `canonical → discovered field` automatically server-side; persist per workspace. Only genuinely low-confidence rows surface for a one-tap confirm — the rest is silent.
4. **Write through** honoring value formats (select-by-id vs text); unmapped canonical fields are flagged, not dropped.

This is what lets the skill **auto-adjust to what the team uses and the fields they actually have** — present fields get set, absent ones degrade gracefully, ambiguous ones get a one-tap confirm.

## Round-trip / sync contract
- **Push:** create or update items, mapping canonical → tool fields above; store the returned tool id on the canonical ticket for future updates (idempotent — never create a duplicate on re-sync).
- **Pull:** read status, assignee, and comments back; reconcile (last-writer-wins with a visible conflict flag).
- **Unsupported field:** if a tool lacks a field (e.g. cycle/sprint on Asana/Monday), map to the closest container and **flag it in the ticket**, don't drop it.
