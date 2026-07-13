# PRD: Self-Serve Team Workspaces

## Part A (human-readable PRD)

### 1. Summary
Let a customer admin create and manage multiple isolated team workspaces under a
single account, invite teammates by email, assign roles, and move existing
projects between workspaces — without contacting support. Today every account is
a single flat workspace, which blocks agencies and multi-team customers who need
separation of billing, membership, and data.

### 2. Background & signals
- 41 support tickets in the last quarter asking "how do I separate my teams?"
  (Signal: Zendesk tag `workspace-split`, Source: support export 2026-06).
- Top 3 churned enterprise logos all cited "no team isolation" in exit surveys
  (Signal: Gong deal-loss notes, Source: revops).
- [NEED] current p95 project count per account — pulling from analytics.

### 3. Goals
- An admin can self-serve the full workspace lifecycle (create, rename, archive).
- Membership and roles are scoped per workspace, not per account.
- Zero data leakage across workspaces (hard tenant boundary).

### 4. Non-goals
- Cross-workspace analytics roll-ups (a later phase).
- Per-workspace billing/invoicing (billing stays account-level for v1).

### 5. Requirements

| # | Requirement | Priority | Signal |
|---|-------------|----------|--------|
| R1 | Admin can create a new workspace with a name and slug | urgent | Zendesk workspace-split |
| R2 | Admin can rename and archive a workspace (archive is reversible) | high | Zendesk workspace-split |
| R3 | Admin invites a teammate to a workspace by email; invite expires in 7 days | urgent | Gong deal-loss |
| R4 | Roles (admin / editor / viewer) are assignable per workspace | high | Gong deal-loss |
| R5 | A user who belongs to multiple workspaces can switch between them | high | support export |
| R6 | Projects can be moved from one workspace to another, preserving history | normal | support export |
| R7 | Data access is hard-scoped by workspace — no cross-workspace reads | urgent | security review |
| R8 | Workspace list shows member count and last-active timestamp | low | design review |
| R9 | Removing a member revokes their access immediately across sessions | high | security review |
| R10 | Audit log records create/rename/archive/invite/role-change events | normal | compliance |
| R11 | Admin can set a default workspace new members land in | low | onboarding feedback |
| R12 | Email notifications fire on invite, role change, and removal | normal | support export |

### 6. Open questions
- Should archived workspaces count against the account's plan limits? [NEED]

## Part B (machine-readable Implementation Spec)
Inherit acceptance criteria from this spec's tests.

- E1 (R1): WHEN an admin submits a unique workspace name and slug, the system
  SHALL create the workspace and redirect to it.
  - test: creating with a duplicate slug returns a validation error [failure]
  - test: slug is normalized to lowercase-kebab [edge]
- E2 (R3): WHEN an admin invites an email, the system SHALL send an invite token
  valid for 7 days.
  - test: an expired token cannot be redeemed [failure]
  - test: re-inviting the same email reissues rather than duplicates [edge]
- E3 (R7): WHERE a request targets a workspace the user is not a member of, the
  system SHALL return 404 (not 403, to avoid leaking existence).
  - test: cross-workspace project read returns 404 [failure]
- E4 (R9): WHEN a member is removed, the system SHALL invalidate their active
  sessions for that workspace within 5 seconds.
  - test: an in-flight request after removal is rejected [failure]
