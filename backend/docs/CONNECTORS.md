# Third-party connectors

How Sprntly registers and authenticates with Google Drive, Figma, GitHub,
Jira, and Confluence. This is the operator guide — read it before clicking
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

## Google Meet

Meeting transcripts, read from the **Meet REST API v2** (`https://meet.googleapis.com/v2`, GA).
Auth module `app/connectors/google_meet.py`, puller `app/kg_ingest/pullers/google_meet.py`,
provider key `google_meet`.

**Shares the Drive connector's Cloud project and OAuth client.** Same
`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`, a **second redirect URI**, and a
separate `connections` row. Do not create a second OAuth client — Google shows
the granted scope list per authorization, so a Drive-only customer is never
asked for Meet permissions and vice versa. (This mirrors the correction
a1e16c40 made about sharing the Jira app with Confluence.)

The **scope lists are not shared and must never be merged.**
`google_oauth.DRIVE_SCOPES` must not grow a Meet scope: scopes bake into a token
at consent and a refresh carries the old set forward, so widening that constant
would leave every already-stored Drive token claiming a capability it does not
have — silent 403s on connections whose probe reads healthy.

### Operator setup

1. **Enable the Meet API** on the existing Cloud project:
   <https://console.cloud.google.com/apis/library/meet.googleapis.com>. Nothing
   works before this, and the failure looks like an auth problem (403), not a
   missing-API one.
2. **Add the redirect URI** to the existing OAuth client's *Authorized redirect
   URIs*: `https://api.sprntly.ai/v1/connectors/google-meet/callback`
   (and the staging equivalent).
3. **Declare the scope and submit for verification.**
   `https://www.googleapis.com/auth/meetings.space.readonly` is **sensitive**
   tier. Until Google approves it — roughly 10 business days, longer if they ask
   for a demo video — every user sees the unverified-app warning screen and the
   app is capped at 100 users.
4. Set `GOOGLE_MEET_OAUTH_REDIRECT_URI`.

### Scopes

```
https://www.googleapis.com/auth/meetings.space.readonly   <- reads everything
openid
https://www.googleapis.com/auth/userinfo.email
https://www.googleapis.com/auth/userinfo.profile
```

The trio after the Meet scope is **not optional**. Google auto-adds them to the
granted set for any client that is also a sign-in client (ours is), so
requesting the Meet scope alone makes the granted set a *superset* of the
requested one, and `google-auth-oauthlib` raises "Scope has changed" at token
exchange. `google_oauth.py:26-33` carries the same note for Drive. We also get
the connecting user's verified email from the ID token, saving a round trip.

`meetings.space.created` is deliberately **not** requested — it covers spaces
this app created, and Sprntly creates no meetings.

**We will never take a restricted Drive scope.** Recordings (the MP4) and Gemini
smart notes live in the organizer's Google Drive and are only reachable via
`drive.readonly` or `drive.meet.readonly`, both **restricted** tier. Taking one
would put this entire OAuth client — the Drive connector included — through an
annual, paid **CASA** security assessment, plus the restricted-scope review.
The transcript *text* comes straight from the Meet API with no Drive access at
all, and that is the part worth having. Recordings and smart notes are therefore
permanently out of scope: a deliberate business decision, not a backlog item.

### Coverage — read this before debugging a "broken" sync

**Organizer-only.** `conferenceRecords.list` returns only conferences where the
authenticating user was the **organizer** — not meetings they merely attended,
and never a colleague's. There is no admin or account-wide listing equivalent to
Zoom's `:admin` scopes, and no scope that would add one. Each teammate whose
meetings should reach Sprntly connects their own Google account. A PM who chairs
nothing legitimately syncs zero meetings, and that is a healthy connection.

**30-day retention, hard.** Google deletes conference records *and* transcript
entries 30 days after the conference ends. There is no historical backfill and
there never can be; a first sync reaches back 30 days and that is the entire
corpus. Because "everything that exists" and "the last 30 days" are the same
set, the puller keeps no incremental cursor — it walks the full window every run
and lets the runner's content-hash ledger make re-seen records free.

**Customer-side requirements**, all three of which produce an empty-but-healthy
connector when unmet:

- Google Workspace **Business Standard or higher**. Business Starter and
  personal Gmail accounts cannot record or transcribe at all.
- **"Record the transcript" switched on before the meeting starts.** Google will
  not transcribe a call retroactively.
  <https://support.google.com/meet/answer/12849897>
- The Meet API not blocked for the Workspace by its admin.

### Tokens

Standard Google OAuth: `access_type=offline` + `prompt=consent` (both required
to be issued a refresh token; without the forced prompt a *re*-authorization
silently omits one). Access tokens last ~1 hour.

Google refresh tokens **do not rotate** — no rotation-strand hazard, unlike Zoom
and Atlassian. They die on user revoke, six months unused, or eviction by the
100-tokens-per-account-per-client cap. **But the refresh response omits
`refresh_token` entirely**, so a caller that stores it verbatim blanks the stored
one and reaches the same dead end by a different road; `token_payload_to_store`'s
`keep_refresh_token` is what prevents that and is load-bearing on every refresh
path (`sync_context`, `connector_probe`, `auto_sync._maybe_refresh_token`).

While the consent screen is in **Testing** publishing status, refresh tokens
expire after **7 days**. Expect this during development and before verification
lands — it is the likeliest cause of "it worked last week".

### Health probe

`connector_probe` lists **one conference record**, not an identity call. The
userinfo endpoint answers on `userinfo.email` while every meeting read answers on
`meetings.space.readonly`, and those genuinely come apart — the Meet API can be
disabled on the project or blocked for the Workspace without touching sign-in.
An identity-only probe would report green on a connector whose every sync 403s,
which is exactly defect #2 of the Confluence granular-scopes incident
(a1e16c40). An **empty** conference list is healthy.

### Ingestion

`pull(company_id)` yields one `RawRecord` per conference:
`conferenceRecords.list` (30-day filter) → `participants.list` (the join table
that turns a transcript entry's participant resource name into a display name)
→ `transcripts.list` → for each transcript in state `FILE_GENERATED`,
`transcripts.entries.list` **paged to the 100 maximum**. Entry paging is
mandatory, not an optimisation: `pageSize` defaults to **10**, so an unpaged
call returns the first ten seconds of a meeting while looking exactly like a
complete short one.

A conference with no finished transcript still yields a record whose text says
so in words — never a silent skip. The commonest cause is a Meet setting the
customer can change, and dropping those meetings would present a half-empty
corpus as a complete one with nothing to explain the gap.

Not implemented, deliberately: recordings/video, Gemini smart notes, in-meeting
chat (not exposed by the API), whole-org coverage (impossible), live chat lookup
(`connector_lookup` lists Meet as `DEFERRED`), and webhooks / Workspace Events
subscriptions.

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

## Zoom

Sprntly reads **cloud recordings and their transcripts** via a Zoom
Marketplace **General (admin-managed)** app. Create it at
<https://marketplace.zoom.us/develop/create>, named e.g. `Sprntly`.

**It must be admin-managed, not user-managed.** A *user-managed* app can only
be granted user-level scopes — it cannot hold the `:admin` variants this
connector requires, so the scopes simply will not be offered and the connector
will only ever see the connecting person's own recordings. If you find yourself
unable to select `cloud_recording:read:list_user_recordings:admin`, this is
why; changing it later means re-creating the app and every customer
reconnecting.

**Not a Server-to-Server app either.** S2S cannot hold the granular
`cloud_recording:*` scopes at all — they are only offered on General app types.

### App settings

- **App type**: General → **admin-managed** (account-level install)
- **Redirect URL for OAuth** + **OAuth Allow List**:
  `https://api.sprntly.ai/v1/connectors/zoom/callback` (production), plus a
  localhost URL for dev, e.g.
  `http://localhost:8000/v1/connectors/zoom/callback`. Both fields — a URL in
  the redirect field but missing from the allow list fails with
  `Invalid Redirect (4700)`.

### Scopes — granular only

New Marketplace apps are **granular-only**. The classic family
(`recording:read:admin`, `user:read:admin`) is not selectable, so asking for it
fails at the *consent screen*, not at the API. Declared in
`app/connectors/zoom_oauth.py::ZOOM_SCOPES`; the app's enabled scopes must be a
superset.

| Scope | Why |
|---|---|
| `cloud_recording:read:list_user_recordings:admin` | `GET /users/{userId}/recordings` — the per-host listing this connector rests on |
| `cloud_recording:read:list_recording_files:admin` | `GET /meetings/{meetingId}/recordings` — one meeting's files, including the `.VTT` transcript |
| `user:read:list_users:admin` | `GET /users` — the host picker |
| `user:read:user:admin` | `GET /users/me` — the connection's account label |

**Every scope is an `:admin` scope, deliberately.** That is what lets ONE
connection read every host's recordings rather than only the connecting
person's — a PM's own Zoom account sees almost no sales calls. The cost is
that **whoever clicks Connect must be a Zoom account owner or admin**, and it
is why `zoom` is not in `routes/connectors._PERSONAL_PROVIDERS`.

**Scopes are baked into the token at consent.** Changing this list means every
existing connection must **reconnect** — a refresh carries the old set forward.

### Admin pre-approval, and the three failure codes

An **unpublished** app must be pre-approved on the customer's Zoom account
(Marketplace → Manage → *Approved Apps*) before anyone there can authorize it.
Zoom usually blocks this at its own consent screen without redirecting; when it
does come back, it comes back through our callback.

A failed consent redirects to `/connectors/return` with one of **three stable
codes**, because they need three different sentences and one catch-all would
send people to the wrong place:

| Code | When | What the user must do |
|---|---|---|
| `zoom_consent_declined` | Zoom sent `error=access_denied` — the user clicked **Decline** | Try again and accept. Nothing is wrong. |
| `zoom_app_not_approved` | The error prose carries approval language, or the token exchange failed with it | Ask a Zoom admin to approve Sprntly in the Marketplace |
| `zoom_oauth_failed` | Anything else | Honest generic failure |

Zoom's raw error string is **never** forwarded — it changes without notice and
would land straight on a screen. Note `unauthorized_client` on its own is
deliberately *not* treated as an approval failure: that code means the grant
type is not enabled on **our** app, a Sprntly-side misconfiguration that no
amount of customer-admin approving would fix.

### Env vars

| Var | Source |
|---|---|
| `ZOOM_CLIENT_ID` | App → *App Credentials* → Client ID |
| `ZOOM_CLIENT_SECRET` | App → *App Credentials* → Client Secret |
| `ZOOM_OAUTH_REDIRECT_URI` | matches the app's Redirect URL exactly |

### Token lifecycle

Access tokens last **1 hour**; refresh tokens last **90 days and ROTATE on
every refresh**. The whole new payload is persisted on every refresh — a
throwaway refresh *spends* the stored token and the connection dies at the next
cycle, with nothing failing at the moment the mistake is made. Refresh happens
in the health probe (`connector_probe.py`), in `auto_sync._maybe_refresh_token`
before a sync, and in `zoom_oauth.sync_context()`. A rejected refresh raises
`ZoomAuthExpiredError` (which carries `status_code = 401`, so `auto_sync` picks
the reconnect branch) → the UI prompts a reconnect.

The client authenticates to the token endpoint with **HTTP Basic**
(base64 `client_id:client_secret`), not credentials in the body. The body form
returns `invalid_client`, which reads like a wrong secret rather than a wrong
auth style.

One Zoom-specific obligation, shared with Confluence: the encrypted token
payload also carries **`company_id`**, because that is the credential the KG
puller will be handed (it needs the host selection off the connection config,
which a lone access token can't reach). Any code path that rewrites the payload
must preserve it — see `zoom_oauth.token_payload_to_store`.

Disconnect calls `POST https://zoom.us/oauth/revoke` **before** deleting the
row. A refresh token we merely forget stays live on Zoom's side for the rest of
its 90 days. The revoke is best-effort: if it fails we still delete, because
keeping our copy of the credential is the worse outcome.

**Reconnecting must not reset the host selection.** `upsert_connection` replaces
`config_json` wholesale, and the 90-day refresh expiry means every long-lived
customer reconnects on a schedule — so the callback reads the existing config
and merges into it. Without that, a workspace that narrowed sync to three sales
hosts would silently widen to *every* host once a quarter (an empty selection
means all hosts), with no event to trace it to.

### What is cached on the connection

Only `{id, email, account_id}` from Zoom's `/users/me` payload
(`zoom_oauth.identity_to_store`). `config_json` is returned **verbatim to every
company member** by `GET /v1/connectors`, and Zoom's user object also carries
the admin's personal meeting id, personal meeting URL, phone number, department
and job title. Caching it whole would publish one person's contact details to
the whole workspace as a side effect of connecting a recordings integration.

`id` is the load-bearing field — see the probe below.

### Health probe

`connector_probe` lists **one recording** for one host (`page_size=1`), not the
identity endpoint. `GET /users/me` answers on `user:read:user:admin` while every
recording read answers on the `cloud_recording` scopes, so an identity-only
probe reports a connection green while every sync fails — the exact defect that
shipped on Confluence (`a1e16c40`).

Two details that keep it from failing green a *different* way:

- It addresses the **real userId** cached at connect, not `me`. An
  admin-managed app is documented to pass an explicit userId; `me` often works
  but is not guaranteed to resolve. (`me` remains the fallback for connections
  made before the id was stored.)
- It passes `allow_missing=False`, so a **404 raises** instead of collapsing to
  an empty list. `api_get` normally swallows 404 into `{}`, which is right for a
  sync racing a deleted recording and catastrophic here: an unresolvable path
  would read as "this host recorded nothing", which reads as *healthy*.

An **empty** recording list is still healthy: an account with nothing recorded
this month is a truthful state, not a broken credential.

### API caveats

- **A missing scope arrives as HTTP 400, not 401.** Zoom answers
  `400 {"code":4711,"message":"Invalid access token, does not contain
  scopes: …"}`. `api_get` inspects the body of a 400 for code `4711`/`4700` or
  the phrase `does not contain scopes` and raises `ZoomAuthExpiredError`;
  every other 400 stays a 502. Without that mapping the picker returns 502
  instead of the reconnect prompt, `auto_sync`'s `getattr(exc, "status_code")`
  takes the "genuine error" branch, and — worst — the probe raises an
  `HTTPException` that `connector_health`'s fail-open catch swallows, leaving a
  wholly broken connector showing **green**.
- **The recordings listing is windowed to ONE MONTH.** `from`/`to` spanning
  more is rejected outright, not truncated, so a longer backfill is several
  calls. `zoom_oauth.window_bounds()` clamps centrally so no caller can trip it.
- **Meeting UUIDs need double URL-encoding** when they start with `/` or
  contain `//` (they are base64, so this is common). Miss it and Zoom's router
  mis-splits the path and answers 404 for a meeting that plainly exists — the
  usual cause of "the recording is right there and the API says it isn't". See
  `zoom_oauth.encode_meeting_uuid`.
- **Only Licensed hosts** (Zoom user `type == 2`) can record to the cloud. A
  Basic host with zero recordings is expected, not a bug — the picker surfaces
  `licensed` so this is visible rather than mysterious.
- **Transcripts are WebVTT**, downloaded from `download_url` with the same
  bearer token in an **Authorization header**. The `?access_token=` query form
  in Zoom's older docs puts a live credential in every proxy and access log on
  the path, so it is deliberately not used. A `download_url` is itself a
  credential-bearing link to customer conversation and is never logged.
- **No webhooks yet.** Sync is poll-only, via the scheduler's
  `refresh_connectors` job.
- Typed `meetings` in `connectors/catalog.py` alongside Fireflies and Gong,
  which makes it **evidence-bearing**: what a customer actually said on a call
  is measured first-party signal, so Zoom alone can satisfy the Top Insights
  brief data-source gate.

### Host selection

`GET /v1/connectors/zoom/users` lists the account's active hosts (readable by
any member); `POST` the same path saves the selection (admin-only, **max 100
hosts** → 422 past that, because the puller pays one windowed recordings call
per selected host per pass). Stored on the connection config as
`sync_user_ids` + `sync_user_names`.

The GET response's size fields mean three different things and are not
interchangeable:

- `total` — how many hosts we **fetched**, not how many exist.
- `fetch_capped` — Zoom still had pages when the listing budget (4 × 300) ran
  out. On a 5,000-host account this is the difference between "showing the
  first 500 of at least 1,200" and a flat lie about the customer's own org.
- `truncated` — the response itself was cut to the 500-host picker limit.

`selected_names` is returned alongside `selected_ids` deliberately: the listing
is active-only, so a host who has since been **deactivated** is absent from
`users`. Without their stored name the picker could only render a bare opaque
id — or silently show a shorter selection than the one actually in force. That
is the entire reason the names are persisted.

**An empty selection means every host on the account.** That is the
backwards-compatible default, and it is what a connection made before the
picker existed has.

### Current scope

Connect, disconnect, health probe, host picker. **No KG puller yet** — a
connected Zoom shows healthy in Settings → Connectors and ingests nothing until
the puller slice lands. No live chat reads either: `zoom` sits in
`connector_lookup.DEFERRED`, so a chat question about it gets the honest "it
syncs but I can't query it live yet" rather than a KG-flavoured guess. No write
path — Sprntly requests no Zoom write scope at all.
