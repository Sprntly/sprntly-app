# Third-party connectors

How Sprntly registers and authenticates with Google Drive, Figma, GitHub,
Jira, Confluence, and Marvin. This is the operator guide — read it before clicking
through any provider's developer-settings UI.

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

---

## Confluence

Sprntly connects to Confluence Cloud via an **Atlassian OAuth 2.0 (3LO)**
app — a **second, separate** integration from the Jira one. Register at
<https://developer.atlassian.com/console/myapps/> → **Create** →
**OAuth 2.0 integration**, named e.g. `Sprntly (Confluence)`.

### Why a separate app (vs adding Confluence scopes to the Jira app)?

- **One app, one callback URL.** An Atlassian 3LO integration carries
  exactly one Callback URL. Sharing would force both connectors through a
  single `/atlassian/callback` that disambiguates on the state JWT's
  `provider` claim — and `jira_oauth.verify_oauth_state` deliberately
  hard-rejects a state whose provider isn't `jira`. That is a refactor of
  a shipped connector to enable a new one.
- **Consent blast radius.** One app declaring both products' scopes means
  a customer connecting only Jira is asked to grant
  `read:confluence-content.all`. Over-broad, for zero benefit.
- **Independent kill switch.** `confluence_configured()` returning False
  disables Confluence without touching Jira.

There is deliberately **no** `CONFLUENCE_CLIENT_ID or JIRA_CLIENT_ID`
fallback in `config.py`: it would produce a silent misconfiguration where
the consent screen 400s on undeclared scopes with no clue why.

### App settings

- **Name**: `Sprntly (Confluence)`
- **Callback URL** (Authorization → OAuth 2.0 (3LO)):
  `https://api.sprntly.ai/v1/connectors/confluence/callback` (production)
  plus one localhost URL for dev, e.g.
  `http://localhost:8000/v1/connectors/confluence/callback`.

### Permissions (scopes) — read the v1/v2 trap first

**Atlassian has two scope families and they are not interchangeable.**

- **Classic** (`read:confluence-content.all`, `read:confluence-space.summary`, …)
  serve the **v1** API under `/wiki/rest/api/...`
- **Granular** (`read:space:confluence`, `read:page:confluence`, …) serve the
  **v2** API under `/wiki/api/v2/...`

Sprntly reads v2 for everything except the current-user lookup (v2 has no
such route), so it needs **granular** scopes plus one classic one. Calling a
v2 endpoint with classic scopes returns:

```
401 {"code":401,"message":"Unauthorized; scope does not match"}
```

which looks like a bad token but is really an authorization mismatch. If you
see that, the scopes are wrong — not the credential.

Add the **Confluence API** under *Permissions*. The console shows **Classic**
and **Granular** as separate tabs on the same app; you need entries from
both. Declared in `app/connectors/confluence_oauth.py::CONFLUENCE_SCOPES`;
the app's declared scopes must be a superset or the consent screen 400s.

| Scope | Tab | Why |
|---|---|---|
| `read:space:confluence` | Granular | `GET /wiki/api/v2/spaces` — the space picker |
| `read:page:confluence` | Granular | `GET /wiki/api/v2/pages` (and blog posts) |
| `read:blogpost:confluence` | Granular | Declared for safety — the scopes reference lists it while the endpoint doc claims `read:page` covers it |
| `read:confluence-user` | Classic | `GET /wiki/rest/api/user/current` for the account label |
| `search:confluence` | Classic | CQL search (`GET /wiki/rest/api/search`) — powers chat's live wiki search. v2 has no search endpoint |
| `offline_access` | — | Get a **refresh token**; access tokens last ~1 h |

As with Jira, `offline_access` plus `prompt=consent` on the authorize URL
are what make Atlassian return a refresh token.

**Scopes are baked into the token at consent.** Changing this list means
every existing connection must **reconnect** — refreshing carries the old
scope set forward, so a stale connection keeps 401ing until the user
re-authorizes.

The health probe calls `list_spaces(limit=1)` rather than the identity
endpoint precisely because of this split: an identity-only probe answers on
the classic scope and would report a connection healthy while every sync
401s.

### Env vars

| Var | Source |
|---|---|
| `CONFLUENCE_CLIENT_ID` | App → Settings → *Client ID* |
| `CONFLUENCE_CLIENT_SECRET` | App → Settings → *Secret* |
| `CONFLUENCE_OAUTH_REDIRECT_URI` | matches the app's Callback URL exactly |

### The cloud_id quirk

Identical to Jira's (see above) with a different path suffix. REST calls go
to `https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/...`
(v2) or `.../wiki/rest/api/...` (v1). We resolve `cloud_id` via
`GET /oauth/token/accessible-resources` at connect time and cache it in
`connections.config_json.cloud_id`.

Note the current-user call uses the **v1** endpoint
(`/wiki/rest/api/user/current`) on purpose: the v2 API has no current-user
route.

### Token lifecycle

Same as Jira: ~1 h access tokens, **rotating** refresh tokens, so the whole
payload is persisted on every refresh. Refresh happens in the health probe
(`connector_probe.py`) and — once the ingest puller lands — before a KG
sync. A rejected refresh raises `ConfluenceAuthExpiredError` → the UI
prompts a reconnect.

One Confluence-specific obligation: the encrypted token payload also
carries **`company_id`**, because that is the credential the KG puller will
be handed (it needs the connection's config, which a lone access token
can't reach). Any code path that rewrites the token payload must preserve
it — see `confluence_oauth.token_payload_to_store`.

### Caveats

- **We see exactly what the connecting user sees.** 3LO acts as that
  person: space permissions and per-page restrictions are enforced by
  Atlassian. There is no scope that widens this, and none that narrows it
  to particular spaces. So coverage depends on *who clicked Connect*, and
  it changes silently if their permissions change. This is the first thing
  to check when a customer says "Sprntly is missing our X docs."
- **No webhooks.** Confluence webhooks are a Connect/Forge descriptor
  feature; a plain 3LO app cannot subscribe. Sync is poll-only, via the
  scheduler's `refresh_connectors` job.
- **Page bodies are never plain text** — `storage` (XHTML plus
  `<ac:*>`/`<ri:*>` macro tags), `atlas_doc_format` (ADF, arriving as a
  JSON *string*), or `view` (rendered HTML).
- Typed `documents` in `connectors/catalog.py`, which makes it
  **non-evidence**: like Notion and Google Docs, Confluence alone cannot
  satisfy the Top Insights brief data-source gate. That is deliberate — a
  page asserting a customer problem is the author's claim about it, not
  measured proof.

### Ingestion

`app/kg_ingest/pullers/confluence.py`, registered in `runner.PULLERS`.
Pages and blog posts, bodies included, from the selected spaces.

The credential the puller is handed is the **company id**, not a token —
`runner.token_for` passes exactly one field of the encrypted payload, and
a Confluence pull also needs the site id and the space selection off
`connections.config`. `confluence_oauth.sync_context()` resolves all of
it (refreshing and persisting an expiring token on the way). The
`uploads` puller uses the same trick.

Caps, all in the puller module: 25 spaces, 250 pages per space per kind
(5 pages of 50), 4 000 chars per record, and a global 500-record valve.
The valve matters because the content-hash ledger makes *re-*syncs free
but the **first** sync pays the LLM for everything.

Signals carry `origin="connector"` **plus `channel="upload"`** (see
`runner._DOCUMENT_PROVIDERS`). A wiki is the same evidentiary class as a
manual upload, and without the channel stamp connecting Confluence would
silently revoke the brief gate's upload-only relaxation — briefs would get
*stricter* the moment a tenant added their wiki.

No per-space watermark, deliberately: the ledger already makes an
unchanged page cost zero LLM, `sort=-modified-date` keeps a truncated
space fresh, and a watermark would blind us to pages *moved* into a space
(old modified-date, never seen again).

### Space selection

`GET /v1/connectors/confluence/spaces` lists what the connected account
can read (readable by any member); `POST` the same path saves the
selection (admin-only). Stored on the connection config as
`sync_space_ids` + `sync_space_keys` — keys kept so a space that later
becomes unreadable is reported by name.

**An empty selection means every readable space.** That is the
backwards-compatible default, and it is what a connection made before the
picker existed has. Personal spaces (`~accountid`) are always excluded.

### Live chat reads

`app/connector_lookup/confluence.py` over `app/connectors/confluence_fetch.py`.
Four read-only tools: `confluence_search` (CQL), `confluence_list_pages`,
`confluence_list_spaces`, `confluence_get_page`.

**Two readers, on purpose.** The KG holds *extracted signals* — atomic facts
the extractor pulled out of pages — not the pages. "What does our onboarding
spec actually say" is a question about the document, and only a live read
answers it. The sync answers "what does the company believe, across every
source".

Reads are bounded by the space selection *and* by the connecting user's own
Confluence permissions. The adapter's system block tells the model to say so
rather than conclude the wiki is silent on a topic.

**Search degrades honestly.** CQL search needs the classic `search:confluence`
scope, added after the first connections were made. A token without it makes
`search_pages` return `available=False`, and the adapter tells the model
search could not run and to fall back to listing. Reporting that 401 as an
empty result set would have chat confidently state a wiki says nothing about
something it documents thoroughly — "we found nothing" and "we could not
look" are different answers.

### Current scope

Connect, disconnect, health probe, KG ingest, space picker, live chat reads.
No write path — Sprntly requests no Confluence write scope at all.

---

## Marvin

Marvin (<https://heymarvin.com>) is a customer-insights / UX-research
repository — interviews, surveys, tagged notes, and the synthesized
Insight reports a research team writes on top of them. It is wired as a
`customer-voice` connector, so it counts as evidence for the Top Insights
brief.

**There is nothing to register.** Marvin has no developer portal, and
this is the only connector where an operator does not create an app by
hand. Read the rest of this section before assuming anything transfers
from the other providers.

### Why MCP instead of a REST API

Marvin publishes no REST API. Their marketing mentions an "open API" and
an "Import API (coming soon)", but the Import API is *inbound* (pushing
data into Marvin) and their help centre documents 33 integrations with no
developer endpoints at all. Their **MCP server** is the only programmatic
read surface that exists, so `kg_ingest/pullers/marvin.py` speaks Model
Context Protocol over Streamable HTTP via `connectors/mcp_client.py`.

Sprntly already *serves* MCP (the top-level `mcp/` package). This is the
opposite direction and shares no code with it: the client here is ~300
lines over `requests` rather than the official async `mcp` SDK, which
would have been the only async dependency in the synchronous connector
layer.

### Two regions, two authorization servers

Marvin runs independent US and EU deployments. A token — and a registered
OAuth client — is valid at exactly one of them, so the user picks the
region **before** the redirect (`RegionPromptModal` in Settings, a select
in the onboarding modal) and it rides in the signed OAuth state.

| | US / Global | EU |
|---|---|---|
| MCP resource | `https://mcp.heymarvin.com` | `https://mcp-eu.heymarvin.com` |
| Authorization server | `https://app.heymarvin.com` | `https://app.eu.heymarvin.com` |

Endpoints are **discovered** per RFC 8414 from
`{issuer}/.well-known/oauth-authorization-server` rather than hardcoded,
so a Marvin-side path change doesn't need a Sprntly deploy. The document
is memoized per process.

### Dynamic client registration (RFC 7591)

Marvin's authorization server advertises a `registration_endpoint`, i.e.
clients register themselves over the wire. On first connect per region,
`marvin_oauth.ensure_client` POSTs a registration and persists the result
in the `oauth_dynamic_clients` table (secret Fernet-encrypted under
`TOKEN_ENCRYPTION_KEY`, like every connector token). Later connects reuse
that row.

Resolution order is: `MARVIN_CLIENT_ID`/`MARVIN_CLIENT_SECRET` env vars →
stored registration → fresh registration.

Two consequences worth knowing:

- **Rotating `TOKEN_ENCRYPTION_KEY`** makes stored client secrets
  undecryptable. That is handled: the read reports "no client" and the
  next connect re-registers. Cost is one orphan client record on Marvin's
  side, not a broken connector.
- **A cold-start race** (two workers, first-ever connect) registers
  twice and one row wins. Both credentials stay valid at Marvin, so this
  is deliberately not locked.

### Scope

`mcp:read` — the only scope the server offers. The connection is
read-only by construction; there is no write path to Marvin.

### PKCE without server-side state

The callback has no session and trusts only the signed `state` JWT. The
PKCE verifier is **derived** from the state's nonce by HMAC under
`JWT_SECRET` (`marvin_oauth.code_verifier_for`), so the callback
recomputes it from the verified nonce. The verifier never travels over
the wire — stronger than stashing it in the state parameter, and with no
store to expire or garbage-collect.

Authorize and token requests both carry `resource=` (RFC 8707), which the
MCP spec requires: it pins the issued token to one MCP server.

### Env vars

| Var | Required | Notes |
|---|---|---|
| `MARVIN_OAUTH_REDIRECT_URI` | **yes** | `https://api.sprntly.ai/v1/connectors/marvin/callback`. The only genuinely required setting — it is also what gets registered as the client's redirect URI. |
| `MARVIN_CLIENT_ID` | no | Override for a statically issued partner client, should Marvin ever grant one. Skips dynamic registration entirely. |
| `MARVIN_CLIENT_SECRET` | no | Pairs with the above. |

Changing `MARVIN_OAUTH_REDIRECT_URI` after a dynamic registration means
the stored client's registered redirect no longer matches. Delete the
provider's rows from `oauth_dynamic_clients` to force re-registration.

### The customer-side prerequisite (the common support ticket)

MCP is **off by default** and gated:

- A Marvin **admin** must enable *Settings → Developer → Enable MCP*.
- Enterprise plans get full access; Pro covers admins, full seats,
  collaborators and viewers; guest/temporary users cannot connect at all.

When the toggle is off, OAuth still succeeds and the MCP server then
exposes no tools. Sprntly treats that as a **failed connect** (HTTP 400
with the "ask a Marvin admin to enable MCP" message) rather than storing
a connection that would silently sync zero records forever. The same
check backs the health probe.

### Tool discovery is heuristic, deliberately

Marvin publishes no schema for its MCP tools, and `tools/list` needs a
live token, so there is no correct set of names to hardcode. The puller
resolves capabilities at sync time: it lists the tools, scores each one's
name and description, then reads its `inputSchema` to learn what the
arguments are called. Renames, added tools and per-plan tool subsets all
survive this; a genuine capability removal surfaces as a clear sync
error. `tests/test_marvin_puller.py` pins the behaviour against several
plausible naming conventions.

The capabilities consumed are: list projects, list files, get file
content. Marvin's **Ask AI** tool is deliberately *not* used by the
puller — it is nondeterministic and bills the customer's Marvin account.
It is a better fit for the live connector-lookup adapters.

### What actually gets ingested

Projects (name + description + research questions) and research files,
distilled to their **analysis** fields only — summary, key points,
takeaways, highlights, findings, insights. Marvin holds full interview
transcripts and those are **not** ingested: the field allow-list is the
filter, and prose responses are capped at 4 000 characters. Same
no-raw-dump contract as the Fireflies puller. Pull is bounded at 50
projects / 200 files.

### Token lifecycle

Access tokens are short-lived with a `refresh_token` grant. Refresh
happens in three places, all of which rebuild the whole stored payload
(the puller's packed `marvin_credential` embeds the access token, so a
partial merge would strand it): the auto-sync pre-flight
(`kg_ingest/auto_sync.py`), the reactive 401/403 retry in the same
module, and the health probe (`connector_probe.py`). A rejected refresh
raises `MarvinAuthExpiredError` → the UI prompts a reconnect.

### Caveats

- **No identity.** MCP exposes no "who am I" call, so `account_label` is
  the server name plus the deployment ("Marvin · EU"). There is nothing
  truthful to put there beyond that, and inventing an email would be
  worse than saying less.
- **Double-counting.** Marvin is itself an aggregator (it imports
  Zendesk, Intercom, Gong, Dscout, Qualtrics…). A customer running both
  Sprntly and Marvin may see some evidence arrive twice by different
  paths.
- **Export fallback.** For workspaces that can't enable MCP, Marvin's
  CSV/DOCX/PDF exports drop into the existing `uploads` connector-category
  path — the `voice` category already maps to `customer_voice` with the
  right extractor hint. No new code, and it covers Pro-tier customers.
