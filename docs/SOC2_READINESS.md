# SOC 2 Readiness — Engineering Gap Analysis

**Status:** open · **Owner:** CTO · **Opened:** 2026-09-01
**Driver:** SOC 2 readiness against the Zee Labs policy set (v1.0, effective 2026-08-31 / 2026-09-01).

Work the items in order. Tick a box only when the control is *evidenceable* — a
config an auditor can screenshot, a document in the repo, or a dated record —
not when it "should be fine".

---

## Scope of this document

This is the **engineering slice only**. It was produced by reading this
repository, its CI configuration, and the live GitHub repo settings on
2026-09-01. Everything below was verified, not assumed; the verification
commands are in the appendix.

### Deliberately NOT covered here

These are real SOC 2 requirements that live outside the codebase. They need a
separate owner and a separate tracker — most likely Vanta.

- AWS / infrastructure: IAM policies, MFA enforcement, VPC and security groups,
  CloudWatch log retention, EBS and S3 encryption at rest
- Supabase project settings: backup plan tier, PITR, backup region, RLS posture
- Vanta: risk register, monitors, policy acknowledgement records
- HR evidence: background checks, security training completion, onboarding and
  offboarding tickets
- Vendor agreements, DPAs, executed subprocessor contracts
- Penetration test and quarterly vulnerability scan records
- Quarterly access review evidence
- Device management: MDM, full-disk encryption, antivirus — the Information
  Security Policy asserts all three
- A security review of the auth and tenancy code itself. Tenancy here is
  enforced entirely in the application layer (the backend uses the Supabase
  service-role key, so RLS is bypassed), which makes it the highest-value
  target for a dedicated review. Out of scope for a documentation survey.

---

## P0 — Fails audit today, and is a live risk

### [ ] P0-1 · No branch protection on `main` or `production`

**Verified:** `GET /repos/Sprntly/sprntly-app/branches/{main,production}/protection`
returns `404`. `GET /rulesets` returns `[]`. There is no protection of any kind
on either branch.

**Policy breached — Secure Development Policy, System Change Control Procedures:**

> "Significant code changes must be reviewed and approved by a senior engineer
> or technical lead before being merged into any production branch"

> "Change control procedures shall ensure that development, testing and
> deployment of changes shall not be performed by a single individual without
> approval and oversight."

The second clause is violated by construction: one person can today push
directly to `production`, which deploys to prod.

Both `security-guard.yml` and `repo-hygiene.yml` carry comments asking to be
made required status checks. Neither is. Both guards are currently advisory.

**Fix:** protection rules on `main` and `production` — require a PR, require
at least one approving review, dismiss stale approvals, and require
`security-guard`, `repo-hygiene`, `test-backend` and `test-web` as status
checks. Add a `CODEOWNERS` file so the reviewer requirement resolves to a
named person.

Maps to: SOC 2 CC8.1.

---

### [ ] P0-2 · Repository is PUBLIC, but source code is classified Confidential

**Verified:** `visibility: PUBLIC`, `isPrivate: false`.

**Policy breached — Data Management Policy** lists **"Source code"** under
*Confidential*, and Confidential Data Handling requires:

> "Access is restricted to specific employees, roles and/or departments"

> "Confidential systems shall not allow unauthenticated or anonymous access"

**Access Control Policy** adds:

> "Access to program source code and associated items ... shall be strictly
> controlled" and "All access to source code shall be based on business need
> and must be logged for review and audit."

A public repository satisfies none of that.

**Fix — pick exactly one, and write down which:**

1. Make the repository private.
2. Reclassify source code as Restricted in the Data Management Policy, with a
   stated rationale.
3. File a CTO-approved documented exception naming the compensating controls.

Leaving it as-is is the only wrong answer. Note this also raises the stakes on
P0-4: with no secret-scanning push protection on a public repo, a leaked key is
world-readable the moment it is pushed.

---

### [ ] P0-3 · `CONTRIBUTING.md` is empty and untracked

**Verified:** 0 bytes, untracked — it does not exist on GitHub.

**Policy breached — Secure Development Policy** cites it by name as the
authority for the change-control process:

> "in accordance with the GitHub Pull Request Review Process found here:
> GitHub CONTRIBUTING.md"

The policy points at a 404.

**Fix:** write it. It must actually describe the process the policy claims:
branch naming, PR requirements, who reviews, required status checks, the
verification gate before push, and the migration-safety rules. Much of this
already exists in `CLAUDE.md` and `BRANCHING.md` but in agent-facing form — it
needs a human-facing, auditor-readable version.

---

### [ ] P0-4 · No dependency scanning, secret scanning, or push protection

**Verified:** `secret_scanning`, `secret_scanning_push_protection` and
`dependabot_security_updates` are all `null`. No `.github/dependabot.yml`. No
`pip-audit`, `npm audit`, CodeQL, Trivy, Snyk, gitleaks or trufflehog anywhere
in `.github/` or `scripts/`.

**Policies breached:**

- Secure Development Policy: *"Application code should be scanned prior to deployment."*
- Secure Development Policy, Developer Training: *"prevention of the use of vulnerable libraries"*
- Operations Security Policy, Appendix A, CI/CD Security: *"Dependency Scanning: Scan for vulnerable dependencies during build processes"*

What does exist — `scripts/security/scan-malware.sh` and the repo-hygiene name
guard — is good work, but neither is a dependency or secret scanner.

**Fix:** enable GitHub secret scanning, push protection and Dependabot alerts
(all free on a public repo, three toggles), add `.github/dependabot.yml` for
`pip` and `npm`, and add a scanning step to CI.

Note the constraint from `CLAUDE.md`: **a pin change is its own PR with its own
justification.** Turning the scanner on will produce findings; resist the urge
to mass-bump. `backend/requirements.txt` pins encode real incidents
(`supabase==2.16.0` for pydantic compatibility, `Pillow>=10.3` as a CVE floor).

---

### [ ] P0-5 · Staging shares the production Supabase project

**Verified:** documented as deliberate design in `CLAUDE.md` — staging is
repointed at the prod Supabase project (`vnfnmiauoblodxmjmaqw`), with
*"no prod-data isolation"* as the stated accepted trade-off. Local
`backend/.env` also reads and writes prod.

**Policy breached — Operations Security Policy, Separation of Development,
Staging and Production Environments:**

> "Development and staging environments shall be strictly segregated from
> production SaaS environments to reduce the risks of unauthorized access or
> changes to the operational environment."

> "Confidential production customer data must not be used in development or
> test environments without the express approval of the CTO."

**Secure Development Policy** repeats the requirement: production, test/staging
and development *"shall be logically or physically segregated"*.

**Fix — one of:**

1. Separate the Supabase projects and accept the connector and OAuth
   re-registration cost.
2. Write a CTO-approved exception documenting the business rationale, the
   compensating controls, and the review date.

The second is legitimate and auditors accept it — but only when it is written
down *before* they find it. Right now the contradiction is documented in
`CLAUDE.md` and nowhere else.

Related: **a migration merged to `main` applies to the shared prod database
before any human sees it on prod.** That is a change-management finding in its
own right, and it compounds P0-1.

---

## P1 — Policy asserts a control the code does not implement

### [ ] P1-1 · No security audit log

**Operations Security Policy, Logging & Monitoring** requires production
applications to log:

- user log-in and log-out
- CRUD operations on application and system users and objects
- security settings changes, including disabling or modifying logging
- **application owner or administrator access to customer data (Access Transparency)**
- with user ID, IP address, timestamp, action type, and object of the action
- retained for at least 30 days

**Verified:** across 192 migrations there is no `audit_log` or `access_log`
table. What exists — `agent_decision_log` and `llm_usage_events` — is LLM cost
and decision telemetry, not a security audit trail.

This is a build, not a config. Maps to SOC 2 CC7.2.

---

### [ ] P1-2 · No MFA in the product

**Access Control Policy:** *"All privileged access to production infrastructure
shall use Multi-Factor Authentication (MFA)"*, and under Management of
Privileged Access: *"Enforce Strong Authentication: Require MFA for all
privileged access."*

**Verified:** `web/app/components/screens/app/settings/SecuritySettings.tsx` is
password-change only; its own docstring notes MFA, active sessions and SSO are
not implemented.

Infrastructure-side MFA (AWS, GitHub, Supabase, Google Workspace) is a separate
item and belongs on the infra tracker — but it also needs evidencing.

---

### [ ] P1-3 · Password policy is unimplementable as written — amend the policy

**Access Control Policy, Password Policy** requires:

- passwords expire after 90 days
- prohibit reuse of the last 16 passwords
- lock out after 6 failed attempts

Supabase Auth provides none of the first three out of the box.

**Recommendation: amend the policy rather than build this.** Mandatory periodic
rotation is contrary to current NIST SP 800-63B guidance, and auditors accept a
modern policy that drops it. Keep and evidence the parts that are both current
and achievable: minimum length, breached-password checking (Supabase supports
HIBP), and brute-force lockout.

This is the one item on the list where the correct fix is to change the
document, not the code.

---

### [ ] P1-4 · No customer data deletion path

**Data Management Policy, Appendix A:**

> "Customer accounts and data shall be deleted within 60 days of contract
> termination through manual data deletion processes."

The policy body also requires PII deletion on verified data-subject request.

**Verified:** no deletion script or runbook exists in the repo. With this
schema, "manual" means someone reasoning about foreign-key order under time
pressure — which is how the wrong tenant's data gets deleted.

**Fix:** a documented, ordered, idempotent deletion runbook, and ideally a
script with a dry-run mode.

---

### [ ] P1-5 · No backup or restore evidence

**Operations Security Policy, Information Backup** requires daily backups, an
**annual restore test**, and storage *"in a separate geographic region from the
primary data location"*.

**BC/DR Plan** also commits to an annual disaster recovery test including
backup restoration, and Appendix B sets a Customer Database RTO of 4 hours and
RPO of 24 hours.

**Verified:** nothing in the repo covers backups or restore. Supabase managed
backups probably satisfy the daily requirement, but region separation depends
on the plan tier, and **the restore test needs a dated, documented run**. An
untested backup is not a control.

---

### [ ] P1-6 · Vulnerability remediation SLAs cannot be evidenced

**Operations Security Policy** sets remediation windows: Critical 30 days,
High 30 days, Medium 60 days, Low 90 days, and requires a service ticket per
risk-relevant finding.

With no scanner producing findings (P0-4) and no tracker receiving them, there
is nothing to measure against. This unblocks once P0-4 lands.

---

### [ ] P1-7 · Log retention mismatch

The Data Retention Matrix states CloudWatch application logs are retained 90
days, and Operations Security requires at least 30. The backend actually runs
under systemd on EC2 — journald with default rotation.

**Fix:** verify what is actually retained and for how long, then reconcile the
matrix to reality or ship the log shipping that makes the matrix true.

---

## P2 — Documents an auditor will ask for by name

| # | Document | Cited by | Status |
|---|---|---|---|
| [ ] P2-1 | `CONTRIBUTING.md` | Secure Development Policy | empty + untracked (see P0-3) |
| [ ] P2-2 | Release Checklist | Secure Development — *"Prior to deploying code, a Release Checklist MUST be completed"* | missing |
| [ ] P2-3 | Coding standards | Secure Development — *"adhere to Zee Labs's coding standards ... quality, commenting, and security"* | missing; `CLAUDE.md` is agent guidance, not a standard |
| [ ] P2-4 | Engineering wiki | Secure Development — *"Internal GitHub repository wiki"* | missing; docs scattered across `/docs` and `backend/docs` |
| [ ] P2-5 | `SECURITY.md` | Incident Response Plan reporting channels | missing |
| [ ] P2-6 | `CODEOWNERS` | implied by "senior engineer approval" | missing |
| [ ] P2-7 | Asset inventory | Asset Management Policy | not in repo — confirm it exists in Vanta |

---

## P3 — Channels and comms to stand up

Referenced across four policies. As far as the repository knows, none of these
exist yet.

- [ ] **P3-1 · `#security` Slack channel.** Named in the Information Security
  Policy, Incident Response Plan, BC/DR Plan and Operations Security Policy.
  The InfoSec Policy makes it the primary incident reporting route:
  *"Incidents shall be reported immediately or as soon as possible by posting
  in the #security Slack channel."*

- [ ] **P3-2 · `#security` channel canvas.** Two separate policies point at it.
  IR Plan Appendix A: *"Contacts for IT and Engineering Management as well as
  executive staff can be found in the #security Slack channel canvas."*
  BC/DR: *"Key contacts shall be maintained on the on-call schedule and key
  contacts: in the #security Slack channel canvas."*
  It must contain the escalation contact list and the on-call schedule.

- [ ] **P3-3 · `security@zeelabs.ai` and `support@zeelabs.ai`.** Both are named
  in the Incident Response Plan as reporting addresses. They must exist, route
  to a human, and be monitored.

- [ ] **P3-4 · Anonymous whistleblower submission form.** The Information
  Security Policy says reports *"may be submitted via an anonymous online
  submission form"* — with no link. Stand one up and put the URL in the policy.

- [ ] **P3-5 · Intercom incident queue.** The IR Plan mandates Intercom tickets
  for P1, P2 and P3 severities. Needs the queue and a severity field that
  matches the P0–P3 scale in the plan.

- [ ] **P3-6 · Policy rollout announcement and acknowledgements.** The Human
  Resource Security Policy requires that employees and contractors
  *"formally acknowledge their understanding and acceptance of their security
  responsibilities."* The policies are effective 2026-08-31 / 2026-09-01 — that
  is now, and the acknowledgement record is itself audit evidence.

---

## Vendor and subprocessor list is materially incomplete

The Data Retention Matrix names: Mixpanel, Amplitude, Salesforce, Stripe,
Intercom, Gong, Sentry, Slack, GitHub, AWS.

The codebase actually integrates, at minimum: **Anthropic**, **Supabase**,
Google Drive, Google Meet, Figma, Jira, Confluence, ClickUp, Asana, HubSpot,
Fireflies, Sprinklr, Superset, Zoom.

Anthropic and Supabase are the two most significant subprocessors in the
system — customer data flows through both — and neither appears anywhere in the
policy set.

**Third-Party Management Policy** requires, before sharing Confidential data:

> "Zee Labs shall not share or transmit Confidential data to a third-party
> without first performing a third-party risk assessment and fully executing a
> written contract, statement of work or service agreement"

- [ ] Reconcile the vendor inventory against the connector catalogue
- [ ] Confirm an executed agreement or DPA exists for each
- [ ] Extend the Data Retention Matrix to cover them

---

## Suggested working order

**First pass — hours, not days, and clears the worst:**
P0-1 (branch protection + CODEOWNERS) → P0-4 (scanning toggles) →
P0-3 / P2-1 (`CONTRIBUTING.md`) → P0-2 (repo visibility decision)

**Second pass:** P0-5 (staging exception memo) → P3-1 through P3-3 (Slack and
email) → vendor list

**Then build:** P1-1 (audit log) → P1-4 (deletion runbook)

**Amend, do not build:** P1-3 (password policy)

---

## Appendix — verification log

Performed 2026-09-01 against `origin/main` at `47cbf5aa`.

```
gh repo view --json name,visibility,defaultBranchRef,isPrivate
gh api repos/Sprntly/sprntly-app/branches/main/protection          # 404
gh api repos/Sprntly/sprntly-app/branches/production/protection    # 404
gh api repos/Sprntly/sprntly-app/rulesets                          # []
gh api repos/Sprntly/sprntly-app --jq '.security_and_analysis'     # all null
wc -l CONTRIBUTING.md                                              # 0
ls supabase/migrations | wc -l                                     # 192
grep -rilE "dependabot|pip-audit|npm audit|snyk|trivy|codeql|gitleaks|trufflehog" .github/ scripts/
```
