# Third-party connectors

How Sprntly registers and authenticates with Google Drive, Figma, and
GitHub. This is the operator guide — read it before clicking through any
provider's developer-settings UI.

All connector tokens are stored Fernet-encrypted in the `connections`
table (one row per provider) keyed by the env var `TOKEN_ENCRYPTION_KEY`.
Account labels (Figma email, GitHub login `@octocat`, Google Drive email)
go in `connections.account_label`; the older `google_email` column is
preserved for the Drive UI.

---

## Google Drive

Already documented in the Drive sync code itself. Uses Google's standard
OAuth2 flow — service account is **not** used. Scopes: the narrow
`https://www.googleapis.com/auth/drive.file` (the app can only read files
the user explicitly picks via the Google Picker — no Drive-wide listing).
Token refresh handled by the `google-auth` library; revocation happens on
disconnect.

Env vars: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_OAUTH_REDIRECT_URI`.

### Ingestion

Picked files sync on two ledgers kept in the connection config
(`app/connectors/google_drive_sync.py`):

- **Corpus copy** (`file_mtime`) — each changed file is downloaded/exported
  and ingested into the dataset corpus, then ledger-marked as a `corpus_doc`
  kg_source so the brief's corpus seed doesn't re-extract it as an upload.
- **Knowledge graph** (`kg_file_mtime`) — changed files are handed to
  `app/kg_ingest/drive_extract.py`, which chunk-extracts each file as its
  own document and writes a per-file
  `kg_source(source_type="google_drive")` provenance row (Drive file id,
  modifiedTime, webViewLink). `kg_file_mtime` advances only after a file
  fully extracts, so lost background threads retry on the next sync.
  Signals carry `origin="upload"` (Drive is a *documents* source — a
  `connector` origin would disable the brief gate's upload-only relaxation;
  see the drive_extract module docstring). Pre-existing connections are
  grandfathered on their first KG-aware sync: already-synced files adopt
  their corpus mtimes instead of re-extracting into near-duplicate signals.

Sync triggers: Picker save, the Settings "Sync" button, the scheduler's
6-hourly `refresh_connectors` job (via `kickoff_sync`, which special-cases
`google_drive` — it has no token puller in `PULLERS`), and the brief's
first-time empty-KG seed (inline).

---

## Figma

OAuth App (not "Plugin"). Register at
<https://www.figma.com/developers/apps>.

### App settings

- **Name**: Sprntly
- **Website URL**: <https://sprntly.ai>
- **Redirect URLs**: `https://api.sprntly.ai/v1/connectors/figma/callback`
  (production) and one localhost URL for dev as needed.

### Scopes

Default scope set, declared in `app/connectors/figma_oauth.py`:

| Scope | Why |
|---|---|
| `files:read` | Read file metadata + structure for design analysis |
| `file_variables:read` | Inspect design tokens / variables |
| `file_dev_resources:read` | Resolve dev-mode resource links |
| `current_user:read` | Display "connected as alice@co.com" in UI |

### Env vars

- `FIGMA_CLIENT_ID`
- `FIGMA_CLIENT_SECRET`
- `FIGMA_OAUTH_REDIRECT_URI`

### Caveats

- Figma has no documented token revocation endpoint, so disconnect just
  drops our row. Users who want to revoke must go to **Figma → Settings
  → Account → Connected apps**.
- Tokens last 90 days; refresh is supported (Figma OAuth refresh-token
  flow). Sprntly currently re-prompts on expiry rather than auto-
  refreshing — see TODO in `figma_oauth.py` if/when long-lived sessions
  matter.

---

## GitHub

Sprntly uses a **GitHub App** (not an OAuth App). The App lets us:

1. Authenticate end users (user-to-server OAuth) — for "Connect GitHub"
   in the connectors UI.
2. Authenticate server-side without a user (installation tokens via App
   JWT) — for creating PRs in private repos and organizations.
3. Receive webhook events when PRs change so we can keep an in-process
   list of open PRs without polling.

### Why a GitHub App (vs OAuth App)?

- **Org installs**: Org owners install once; permissions apply to all
  selected repos. OAuth Apps require per-user repo grants and inherit
  whatever access that user has — fragile.
- **Fine-grained permissions**: App permissions are declared up front and
  consented to once. OAuth scopes (`repo`, etc.) are coarse.
- **Higher rate limits**: 5,000/hour per installation, scaling with org
  size.
- **Webhooks scoped to the installation**: We only see events from repos
  the installer chose.

### Registration

<https://github.com/settings/apps/new> (personal) or
`https://github.com/organizations/<org>/settings/apps/new` (org).

#### Identifying info

- **GitHub App name**: `Sprntly` (must be globally unique)
- **Homepage URL**: <https://sprntly.ai>
- **User authorization callback URL**:
  `https://api.sprntly.ai/v1/connectors/github/callback`
- **Setup URL** (optional, post-install redirect):
  `https://app.sprntly.ai/connectors`
- **Webhook URL**: `https://api.sprntly.ai/v1/connectors/github/webhook`
- **Webhook secret**: 32+ random bytes. Sprntly verifies every payload's
  `X-Hub-Signature-256` against this.

#### Repository permissions

| Permission | Access | Why |
|---|---|---|
| Contents | Read & write | Create branches + commits when authoring a PR |
| Pull requests | Read & write | Open / update / read PRs |
| Metadata | Read-only | Always required by GitHub; lists repos |
| Issues | Read & write | Optional — link PRs to issues, comment |
| Checks | Read-only | Show CI status alongside PR list |

Leave everything else **No access**.

#### Organization permissions

| Permission | Access | Why |
|---|---|---|
| Members | Read-only | Resolve mentions / reviewers for org installs |

#### Account permissions

None.

#### Subscribe to events

Tick:

- `installation` — when admins install, suspend, uninstall the App.
- `installation_repositories` — when repo selection changes (from
  "selected" to "all" or vice versa).
- `pull_request` — opened / edited / synchronize / closed / reopened /
  ready_for_review. Drives `github_pull_requests` table updates.

(Optional, not currently consumed) `pull_request_review`,
`pull_request_review_comment`, `check_suite`.

#### Where can this App be installed?

`Any account` (so customers can install on their own orgs).

#### Expire user authorization tokens

**On** — gives us refresh tokens and 8-hour user-to-server access
tokens. Sprntly stores both and calls `refresh_user_token` in
`github_app.py` when needed.

### Private key

After creating the App, click **Generate a private key**. GitHub gives
you a single `.pem` download — store it. Cannot be re-downloaded.

Set `GITHUB_APP_PRIVATE_KEY_PEM` to the file's contents (literal PEM
with `\n` newlines; `config.py` normalizes either form).

### Env vars

| Var | Source | Notes |
|---|---|---|
| `GITHUB_APP_ID` | App settings page | numeric, top of the page |
| `GITHUB_APP_CLIENT_ID` | App settings page | starts `Iv1.` for older apps, `Iv23l…` for newer |
| `GITHUB_APP_CLIENT_SECRET` | App settings page → "Generate a new client secret" | rotate periodically |
| `GITHUB_APP_PRIVATE_KEY_PEM` | downloaded `.pem` | one of two: literal PEM (multi-line) or `\n`-escaped single-line |
| `GITHUB_OAUTH_REDIRECT_URI` | matches the App's callback URL | `https://api.sprntly.ai/v1/connectors/github/callback` |
| `GITHUB_WEBHOOK_SECRET` | matches the webhook secret you set on the App | 32+ random bytes |

### Two token modes (and when each runs)

```
User clicks "Connect GitHub"
  ──> GET /v1/connectors/github/authorize
       ──> sign_oauth_state()   → HS256 JWT, 10-min TTL
       ──> 302 → https://github.com/login/oauth/authorize?...
  ──> user consents on github.com
  ──> 302 → /v1/connectors/github/callback?code=...&state=...
       ──> verify_oauth_state()
       ──> exchange_code_for_token()
       ──> fetch_authenticated_user()
       ──> store provider="github" row in connections   ← user OAuth token
       ──> 302 → /connectors?connected=github

Server creates a PR (no user present)
  ──> get_installation_token(install_id)
       ──> cache hit?  → return
       ──> cache miss → make_app_jwt()              → RS256, 8-min TTL
                        POST /app/installations/{id}/access_tokens
                        cache (token, expires_epoch)
       ──> requests.post("https://api.github.com/repos/.../pulls",
                         headers=headers_for_installation(id))
```

User-OAuth identifies *which Sprntly user owns this account*. Installation
tokens are what we actually use to read/write repo contents — they're
scoped to the *installation*, not the user.

### Webhook flow

Every event:

1. nginx (api.sprntly.ai) → uvicorn → FastAPI route.
2. `verify_webhook_signature(raw_body, X-Hub-Signature-256)` —
   HMAC-SHA256 with `GITHUB_WEBHOOK_SECRET`, constant-time compare.
   401 on mismatch (GitHub will retry).
3. Dispatch on `X-GitHub-Event`:
   - `ping` → 200, no-op (GitHub fires this once when you save the
     webhook URL).
   - `installation` (created / deleted / suspend / unsuspend /
     new_permissions_accepted) → upsert or delete
     `github_installations` row; on deleted, also
     `clear_installation_token_cache(id)`.
   - `installation_repositories` (added / removed) → re-upsert the row
     with new `repository_selection`.
   - `pull_request` → upsert `github_pull_requests`. Closed PRs keep
     their row with `state='closed'` (or `'merged'`); open-PR queries
     filter on `state='open'`.
   - anything else → 200 with `handled: false`.

GitHub retries on any non-2xx for ~72h with exponential backoff, so
catching/swallowing errors here is preferable to 500ing.

### Install caveats

- **Personal accounts**: the user clicks Install → picks repos → done.
  No admin approval involved.
- **Organizations**: only org **owners** can install. If a non-owner
  tries to install on an org, GitHub queues an approval request. The
  org owner gets an email; the App can't do anything in that org until
  approved.
- **Repository selection**: customers can install on *all repos* or
  *select repos*. Encourage "selected repos" for least privilege; we
  track which mode they chose in `repository_selection`.
- **Marketplace listing** (optional, future): the App can be listed on
  GitHub Marketplace once Sprntly has billing. Free apps just need a
  toggle flip; paid apps require Stripe Connect integration on
  GitHub's side.

### Local testing

You can't point GitHub's webhook at localhost. Two options:

1. **smee.io / ngrok**: forward `api.sprntly.ai/v1/connectors/github/webhook`
   to localhost via a tunnel. Easiest for one-off debugging.
2. **Manual replay**: copy a real event payload from the App's "Advanced"
   → "Recent Deliveries" panel and POST it locally with the right
   `X-Hub-Signature-256` header. The tests in
   `tests/test_routes_connectors_github_webhook.py` show how to compute
   the signature.

### Rotating the webhook secret

1. Generate new secret.
2. In the App settings, paste it as the webhook secret and save.
3. Update `GITHUB_WEBHOOK_SECRET` on the EC2 host and restart
   `sprintly.service`.
4. GitHub then signs new deliveries with the new secret; old in-flight
   retries from before the rotation will 401, which is fine.

### Rotating the private key

1. In App settings → "Private keys" → generate a new key.
2. Replace `GITHUB_APP_PRIVATE_KEY_PEM` on EC2.
3. Restart `sprintly.service` to drop the in-process installation-token
   cache.
4. Wait until any cached tokens expire (≤55 min), then delete the old
   key from the App settings.

---

## Jira

Sprntly connects to Jira Cloud via an **Atlassian OAuth 2.0 (3LO)** app.
Register at <https://developer.atlassian.com/console/myapps/> →
**Create** → **OAuth 2.0 integration**.

### Why 3LO (vs an Atlassian Connect app or API token)?

- **Per-user consent, org-wide reach**: the connecting user grants access
  to the Jira sites they can see; no per-project token juggling.
- **Read + write from one grant**: `read:jira-work` + `write:jira-work`
  cover KG ingest (issues) and pushing generated tickets as issues.
- **Refreshable**: with `offline_access` we get a refresh token, so a
  connection keeps working past the ~1 h access-token lifetime without a
  reconnect.

### App settings

- **Name**: `Sprntly`
- **Callback URL** (Authorization → OAuth 2.0 (3LO) → *Callback URL*):
  `https://api.sprntly.ai/v1/connectors/jira/callback` (production) plus
  one localhost URL for dev as needed, e.g.
  `http://localhost:8000/v1/connectors/jira/callback`.

### Permissions (scopes)

Add the **Jira API** under *Permissions*, then grant these scopes. They
are declared in `app/connectors/jira_oauth.py::JIRA_SCOPES`; the app's
declared scopes must be a superset or the consent screen 400s.

| Scope | Why |
|---|---|
| `read:jira-work` | Read issues + projects (KG ingest, project picker) |
| `write:jira-work` | Create/update issues (push stories + tickets) |
| `read:jira-user` | Resolve the authorizing user (`/myself`) for the label |
| `offline_access` | Get a **refresh token** — access tokens last ~1 h |

`offline_access` plus `prompt=consent` on the authorize URL are what make
Atlassian return a refresh token; without both, every sync past the first
hour would 401.

### Env vars

| Var | Source |
|---|---|
| `JIRA_CLIENT_ID` | App → Settings → *Client ID* |
| `JIRA_CLIENT_SECRET` | App → Settings → *Secret* |
| `JIRA_OAUTH_REDIRECT_URI` | matches the app's Callback URL exactly |

### The cloud_id quirk (important)

A 3LO token authenticates against `api.atlassian.com`, **not** the
customer's `*.atlassian.net` host. Every REST call needs the target
site's `cloud_id`, which is **not** in the token response. We resolve it
via `GET /oauth/token/accessible-resources` at connect time and cache it
in `connections.config_json.cloud_id`; the KG puller (which only carries
the access token) re-resolves it on the fly via `first_cloud_id`. REST
calls then go to
`https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/...`.

### Token lifecycle

- Access tokens expire in ~1 h. Refresh tokens **rotate** — each refresh
  returns a new refresh token, so we persist the whole payload (same as
  the GitHub user token, unlike HubSpot's stable refresh token).
- Refresh happens lazily before a KG sync (`kg_ingest/auto_sync.py`),
  before a push (`stories/push.py::_jira_creds`), and in the health probe
  (`connector_probe.py`). A rejected refresh surfaces as
  `JiraAuthExpiredError` → the UI prompts a reconnect.

### Caveats

- Issue descriptions are **Atlassian Document Format (ADF)**, not
  markdown — `jira_oauth._adf_from_text` wraps plain text into ADF
  paragraphs on create/update.
- `priority` is omitted when unmapped: not every project defines a
  priority field, and Jira 400s on unknown fields.
