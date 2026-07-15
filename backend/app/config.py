from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    openai_api_key: str = ""  # embeddings (text-embedding-3-small)
    # Design Agent uses a dedicated key for cost attribution + per-key
    # rotation at handoff; falls back to anthropic_api_key with a startup
    # warning (see app/design_agent/client.py).
    design_agent_anthropic_api_key: str = ""
    # Spend backstop: hard USD ceiling ABOVE the $0.50 soft cap. When a
    # run's projected next-iteration spend reaches this, agent_loop ABORTS (clean
    # terminal status, partial bundle salvaged) rather than degrade-and-continue.
    # Default 5.00 only catches PATHOLOGICAL runs: the worst observed-legit run
    # hit $0.76 realized → $1.52 projected (2× projection), so the cap MUST stay
    # > $1.52; 5.00 ⇒ abort fires only when realized ≥ $2.50 (well above the worst
    # legit run, 10× the soft cap). Env-overridable via DESIGN_AGENT_HARD_CAP_USD;
    # never lower than the soft cap, and never below the $1.52 legit-run floor.
    design_agent_hard_cap_usd: float = 5.00
    # Vite build budget for a prototype gen. Default 180s — the typical
    # scaffold builds in ~5-15s, but a cold node start, a large single-file emit,
    # or a busy host (esp. the colder prod EC2 build host) can exceed a tighter
    # budget and floor an otherwise-valid build. Env-overridable per
    # environment via DESIGN_AGENT_VITE_BUILD_TIMEOUT_SECONDS. Read at call-time
    # in design_agent/storage.py:_vite_build_sync so it stays tunable + testable.
    design_agent_vite_build_timeout_seconds: int = 180
    # Process-wide cap on in-flight Anthropic model calls (see app/llm.py).
    # Process-wide cap on concurrent in-flight Anthropic streams; calls beyond
    # this QUEUE instead of piling on. The default is conservative for a small
    # box; raise it (env LLM_MAX_CONCURRENCY) on hosts with RAM headroom —
    # measured: 6 concurrent streams used ~80 MB on the 3.8 GB prod box. Values
    # <= 0 fall back to the default (never 0, which would deadlock).
    llm_max_concurrency: int = 3
    # How many of those slots BACKGROUND (warm / pre-generation) calls may hold
    # at once. Bounds warm parallelism while leaving (capacity - bg_cap) slots
    # interactive callers can always reach, so a user's click is never queued
    # behind warming. Default 1 (warm serialized); raise via LLM_BG_CAP to
    # parallelize the per-insight PRD/evidence warm (clamped to capacity-1).
    llm_bg_cap: int = 1
    # Ticket generation strategy. "single" (default) is one big streamed call
    # for the whole set; when ticket_gen_fanout is true the route uses the
    # fan-out path (plan → parallel enrich) which shards the big generation into
    # concurrent shorter streams. Fan-out shards contend for the SAME
    # llm_max_concurrency slots, so raising ticket_gen_max_parallel without also
    # raising llm_max_concurrency just makes shards queue on the gate. Kept off
    # by default until the benchmark confirms the win on the target box.
    # Env: TICKET_GEN_FANOUT / TICKET_GEN_BATCH_SIZE / TICKET_GEN_MAX_PARALLEL.
    ticket_gen_fanout: bool = False
    ticket_gen_batch_size: int = 4
    ticket_gen_max_parallel: int = 4
    # Tier 1 — process-wide cap on how many Design Agent generations may run
    # their HEAVY section (LLM recreate loop + vite build + screenshot) at once.
    # Default 1: on the 2-vCPU prod box, one generation already pins both cores
    # through the vite build, so admitting a second concurrent heavy run is what
    # produced the 504-under-load contention. At 1, a concurrent /locate keeps CPU
    # headroom. Read at CALL-TIME in routes/design_agent.py (the semaphore is
    # lazy-initialised on first use), so a test can monkeypatch this and a future
    # bump (e.g. on a larger box) takes effect without an import-time freeze.
    # Env-overridable via DESIGN_AGENT_GENERATION_CONCURRENCY.
    design_agent_generation_concurrency: int = 1
    # Tier 0 — how long the lifespan teardown waits for in-flight generation
    # to drain on SIGTERM before giving up (deploy/restart graceful-drain). MUST
    # exceed the vite-build subprocess timeout (design_agent_vite_build_timeout_seconds,
    # default 180s) or a build in flight at shutdown is abandoned and the deploy
    # 502s recur — so the default 200s sits above the 180s build budget. On
    # deadline-elapse the teardown does NOT cancel (the vite thread is
    # uncancellable); the startup orphan sweep recovers any left-behind 'generating'
    # row on next boot. Env-overridable via DESIGN_AGENT_DRAIN_DEADLINE_SECONDS so
    # it can be tuned per environment. NOTE: the systemd unit must set
    # TimeoutStopSec > this value (>=220s) or systemd SIGKILLs mid-drain.
    design_agent_drain_deadline_seconds: int = 200
    # Tier 2 — OPT-IN worker queue. When True AND a worker heartbeat is
    # fresh, POST /generate enqueues the generation onto `design_agent_jobs` for
    # a separate `python -m app.worker` process to run, removing the heavy work
    # (LLM loop + vite build + Chromium) from the API request process (the
    # t3.micro 504 contention). Default FALSE: a box that has not deployed the
    # 2nd systemd worker unit must degrade to today's in-process create_task path
    # — and so must a box where the flag is on but no worker is alive (no fresh
    # heartbeat) or the table is missing. Like DESIGN_AGENT_ENABLED, the gate is
    # read at REQUEST/CALL time (os.environ in routes/design_agent.py + the
    # worker), never frozen at import, so a flip takes effect without a code
    # deploy and a reloaded Settings singleton in tests stays honest.
    # Env-overridable via DESIGN_AGENT_WORKER_ENABLED.
    design_agent_worker_enabled: bool = False
    # When True, a backend startup whose prototype template version is greater
    # than an existing 'ready' prototype's stamped version demotes that prototype
    # to 'invalidated' (the View path 404s it → the PRD screen drops to the
    # "Generate" CTA). Default FALSE: a routine template bump is a generation-prompt
    # cache refresh, NOT a render-safety break — the bundle is a self-contained
    # static build that still renders — so existing ready prototypes are PRESERVED
    # across the bump and remain viewable. Set True only for a deliberate breaking
    # change where old bundles must be force-regenerated. Read at lifespan startup
    # in app/main.py. Env-overridable via
    # DESIGN_AGENT_INVALIDATE_PROTOTYPES_ON_TEMPLATE_BUMP.
    design_agent_invalidate_prototypes_on_template_bump: bool = False
    # How many of a fresh brief's top insights get their PRD auto-generated
    # after brief generation (hero first, then confidence). Default 3 = every
    # insight in the brief (the brief surfaces MAX_INSIGHTS=3), so all three
    # points get a PRD automatically. Warm calls run in the LLM gate's
    # background lane so they never delay a user's click. 0 disables.
    prd_warm_count: int = 3
    allowed_origins: str = "http://localhost:3000"
    env: str = "development"

    demo_password: str = ""
    jwt_secret: str = "dev-only-change-me"
    session_ttl_hours: int = 720
    cookie_domain: str = ""

    # Where corpus markdown + uploaded originals live. In prod this is set to
    # /var/lib/sprntly/data so EC2 git pulls don't wipe uploads. Templates
    # (sprntly_prd_template.md, sprntly_evidence_template.md) live here too
    # but ship via the repo; on first boot we seed them if the dir is empty.
    data_dir: str = str(REPO_ROOT / "data")
    # Templates ship in-repo and never get uploaded by users. Keeping them
    # under the repo even when DATA_DIR points elsewhere means template
    # edits flow through normal PRs.
    template_dir: str = str(REPO_ROOT / "data")

    db_path: str = str(REPO_ROOT / "data" / "sprintly.db")

    # Internal service-to-service API (DS Agent → Backend)
    internal_api_key: str = ""
    # Sprntly staff admin surface (/v1/staff + the /admin panel) — a DEDICATED
    # owner-only credential, deliberately separate from normal Sprntly
    # (Supabase) login. POST /v1/staff/login checks the id (constant-time) and
    # the password against staff_admin_password_hash (argon2id — the password
    # hasher already shipped for prototype share passcodes) and mints a
    # short-lived staff JWT (aud=sprntly-staff, signed with jwt_secret).
    # BOTH must be set or the whole surface — login included — 404s
    # (fail closed, invisible), the same posture the old STAFF_EMAILS
    # allowlist had when empty.
    staff_admin_id: str = ""
    staff_admin_password_hash: str = ""
    # Design Agent bundle staging. Supabase Storage is the PRIMARY
    # destination (bucket named by the SUPABASE_STORAGE_BUCKET env var, read
    # directly in design_agent/storage.py). These two settings drive the
    # dev/test FALLBACK used when that env var is unset:
    #   storage_dir        — filesystem root the dist/ bundle is written under
    #   storage_public_url — public base URL the bundle is served from (nginx in
    #                        local dev). Empty → stage_bundle returns a file://
    #                        URL (test-only fallback).
    storage_dir: str = str(REPO_ROOT / "data" / "prototypes")
    storage_public_url: str = ""

    # Google Drive connector (OAuth)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_oauth_redirect_uri: str = ""
    token_encryption_key: str = ""
    frontend_url: str = "http://localhost:3000"

    # Supabase — set in EC2 + GH secrets. Backend uses the service-role
    # key (bypasses RLS) since it's a trusted server, not a browser.
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    # JWT secret from Supabase project settings (Settings → API → JWT Secret).
    # Used to verify access tokens sent as Authorization: Bearer …
    supabase_jwt_secret: str = ""
    # Shadow-write inserts to Supabase alongside SQLite. Reads stay on
    # SQLite. Defaults off so the flag has to be flipped per environment.
    supabase_dual_write: bool = False

    # Figma connector (OAuth 2.0)
    figma_client_id: str = ""
    figma_client_secret: str = ""
    figma_oauth_redirect_uri: str = ""

    # ClickUp connector (OAuth 2.0)
    clickup_client_id: str = ""
    clickup_client_secret: str = ""
    clickup_oauth_redirect_uri: str = ""

    # Jira connector (Atlassian OAuth 2.0 3LO with refresh tokens)
    jira_client_id: str = ""
    jira_client_secret: str = ""
    jira_oauth_redirect_uri: str = ""

    # HubSpot connector (OAuth 2.0 with refresh tokens)
    hubspot_client_id: str = ""
    hubspot_client_secret: str = ""
    hubspot_oauth_redirect_uri: str = ""
    # Space-separated scopes. Minimum is just `oauth`. Add more (e.g.
    # `crm.objects.contacts.read`) when sync features actually need them.
    hubspot_scopes: str = "oauth crm.objects.contacts.read"
    # Which HubSpot OAuth API generation to use. v3 (modern, RFC 7662
    # introspection, 30-minute access tokens, body-only credentials) is
    # the default since legacy v1 endpoints are sunset-pending and new
    # HubSpot accounts can't create legacy public apps. Set to "v1" for
    # backward-compat with older legacy apps still active in production.
    hubspot_oauth_version: str = "v3"

    # Asana connector (OAuth 2.0; ~1h access tokens with long-lived refresh).
    # App registered in the Asana developer console. Scope depends on the
    # app's permission MODE there: full-permissions apps accept only the
    # special "default" scope (granular scopes get `forbidden_scopes`),
    # while scoped-permissions apps take space-separated
    # "<resource>:<action>" scopes that were pre-selected on the app —
    # for those, set ASANA_SCOPES="users:read workspaces:read".
    asana_client_id: str = ""
    asana_client_secret: str = ""
    asana_oauth_redirect_uri: str = ""
    asana_scopes: str = "default"

    # Sprinklr connector (OAuth 2.0; ~30-day access tokens with refresh).
    # Key + secret come from an app registered on dev.sprinklr.com; they are
    # ENVIRONMENT-SPECIFIC — a key minted for one Sprinklr environment is
    # invalid in another, so sprinklr_environment must match the env the app
    # was registered for ("" = production app.sprinklr.com; "prod0"/"prod2"/
    # "sandbox" for the others).
    sprinklr_api_key: str = ""
    sprinklr_api_secret: str = ""
    sprinklr_oauth_redirect_url: str = ""
    sprinklr_environment: str = ""

    # Slack connector (OAuth 2.0 — bot token for message delivery + sync)
    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_oauth_redirect_uri: str = ""
    slack_scopes: str = (
        "chat:write,channels:read,channels:history,"
        "groups:read,groups:history,users:read"
    )
    # Bot scopes (xoxb): send (chat:write) + DM a user (im:write) + read the
    # channels/DMs the bot has been added to (history scopes). Must mirror the
    # Bot Token Scopes configured on the Slack app for the consent screen to
    # grant them.
    #
    # `channels:join` lets the bot self-add to a public channel before posting,
    # so a brief lands even when nobody invited it first (the #1 cause of
    # "notifications never showed up"). `groups:read` lets us list private
    # channels in the picker — the bot still can't self-join private channels
    # (Slack forbids it), so those must be invited manually, but at least they
    # become selectable. `team:read` backs team.info, which the Test-connection
    # health check + the account label call — without it the freshly-minted
    # token gets `missing_scope` on team.info and the connection shows as
    # "slack rejected the stored credential" even though the token is valid.
    # All three are already declared in the Slack app's Bot Token Scopes;
    # existing installs must reconnect to pick them up.
    slack_bot_scopes: str = (
        "chat:write,im:write,channels:read,channels:join,channels:history,"
        "groups:read,groups:history,im:history,mpim:history,users:read,team:read"
    )
    # User scopes (xoxp): read the authorizing user's OWN messages + search,
    # acting as them. Rides on `user_scope=` in the authorize URL; Slack then
    # returns authed_user.access_token. Empty ⇒ no user_scope on the consent
    # screen ⇒ no user token issued (send-only / bot-reads-only install).
    slack_user_scopes: str = (
        "channels:history,groups:history,im:history,mpim:history,search:read"
    )
    # Signing secret (Slack app → Basic Information → App Credentials). Required
    # to verify the request signature on the Events API endpoint (app_uninstalled
    # + app_home_opened). Empty → the events endpoint rejects all requests.
    slack_signing_secret: str = ""

    # Transactional email (Resend) — used for brief notifications + future
    # system mail. Sends via the Resend HTTPS API (api.resend.com), keyed by
    # RESEND_API_KEY, FROM the verified sending domain (mail.sprntly.ai).
    # Empty key ⇒ email delivery is a clean no-op (logged), never an error —
    # so non-prod envs without the key simply skip sending.
    resend_api_key: str = ""
    # Envelope From for outbound brief email. Must be on a Resend-verified
    # domain; defaults to the verified mail.sprntly.ai sender.
    brief_email_from: str = "Sprntly <briefs@mail.sprntly.ai>"

    # Synthetic sign-in monitor (added after the 2026-06-22 incident where a
    # rotated/deleted Google OAuth secret silently broke "Sign in with Google"
    # and no test caught it). Periodically authenticates the Google OAuth client
    # against Google's token endpoint and alerts if the secret is rejected.
    # See app/signin_monitor.py.
    signin_monitor_enabled: bool = True
    signin_monitor_interval_minutes: int = 15
    # Where to email the alert on failure (empty => log-only / no email).
    signin_monitor_alert_email: str = ""

    # Scheduled connector health monitor (app/connector_health.py). Re-validates
    # every active connector's stored OAuth/API token on an interval and persists
    # the result, so the connectors UI surfaces a dead connector proactively and
    # we email a healthy→disconnected transition alert. min_recheck throttles the
    # sweep against the on-open test so the two don't double-probe the same row.
    connector_health_enabled: bool = True
    connector_health_interval_minutes: int = 60
    connector_health_min_recheck_minutes: int = 50
    # FALLBACK alert address only. Disconnect alerts go to each connector's
    # OWNER (resolved from profiles); this catches connectors whose owner email
    # can't be resolved. Empty => fall back to signin_monitor_alert_email; both
    # empty => unrouted connectors are log-only.
    connector_health_alert_email: str = ""

    # In-app feedback / feature-request form (June 20 #13 + #A). Users submit a
    # short message + type (bug / feature / connector request) from the left
    # nav; we store it in the `feedback` table and email it to the team via
    # Resend. Recipient resolution: FEEDBACK_ALERT_EMAIL wins; if unset we fall
    # back to SIGNIN_MONITOR_ALERT_EMAIL (the existing team/ops alert address).
    # Both empty ⇒ storage still happens, email is a clean no-op (logged).
    feedback_alert_email: str = ""

    # Where to email a Design Agent provider hard-stop alert (a billing / credit
    # hard-stop the team must act on). Deduped per class + fail-open in
    # app/design_agent/provider_alert.py. Empty ⇒ alert is a clean no-op (logged).
    design_agent_alert_email: str = ""

    # Which engine produces the weekly brief.
    #   "synthesis" (default) — KG-driven: seed-if-empty → run_synthesis over the
    #                           knowledge graph (kg_signal/kg_entity) → save_brief.
    #   "legacy"              — placeholder corpus→single-Claude-call pipeline,
    #                           kept dormant behind the flag as a fallback.
    # Drives both the UI write endpoints (/v1/brief/regenerate,/generate) and the
    # scheduler cycle; the UI read path (/current,/status,/{id}) is unchanged.
    brief_engine: str = "synthesis"

    # Pipeline scheduler
    scheduler_enabled: bool = False
    pipeline_interval_hours: int = 6
    # Weekly-brief scheduler (v0 checklist 2.4): the brief fires Monday 09:00 in
    # each company's configured timezone (companies.notification_settings.timezone,
    # default UTC). The scheduler ticks every WEEKLY_BRIEF_TICK_MINUTES and, for
    # each company, asks app.brief_schedule.should_run_weekly_brief whether the
    # local Monday-09:00 firing window is open. Must be comfortably smaller than
    # brief_schedule.DUE_WINDOW (1h) so a window is never skipped between ticks;
    # 15 min gives ~4 chances to catch each window even if a tick runs late.
    weekly_brief_tick_minutes: int = 15
    # Ticket tracker sync: every tick, two-way sync each PRD whose tickets were
    # pushed to ClickUp/Jira (prd_ticket_sync rows with auto_sync=true). 15 min
    # by default; raise via TICKET_SYNC_INTERVAL_MINUTES (e.g. 60–120) if
    # tracker API rate limits ever bite.
    ticket_sync_enabled: bool = True
    ticket_sync_interval_minutes: int = 15
    scraping_user_agent: str = "Sprntly/1.0 (product intelligence)"

    # ── Onboarding drip / nudge emails (v0 checklist 2.1) ────────────────
    # Recurring onboarding emails to newly-joined company members on a cadence
    # (default day-1 / day-3 / day-7). Sent via Resend; tracked per member ×
    # step in drip_email_sends so steps never double-send. See app/drip_email.py.
    #
    # Opt-in: the drip scheduler job is registered only when DRIP_EMAILS_ENABLED
    # is true AND SCHEDULER_ENABLED is on (the APScheduler itself must be
    # running). RESEND_API_KEY drives the transport; when unset, sends are
    # recorded as "skipped" so flipping the key on later does not retro-blast
    # historical steps.
    drip_emails_enabled: bool = False
    resend_api_key: str = ""
    # From: header for drip emails. Empty → "Sprntly <onboarding@sprntly.ai>".
    drip_from_email: str = ""
    # Comma-separated day offsets, e.g. "1,3,7". Empty → DEFAULT_CADENCE.
    # Per-company overrides in companies.notification_settings["drip"] win over
    # this (see app/drip_email.py:resolve_cadence).
    drip_cadence_days: str = ""
    # How often the drip job runs. Hourly+ is fine: a step fires the first
    # cycle after a member crosses its day_offset, and de-dup makes extra
    # cycles cheap no-ops.
    drip_interval_hours: int = 6
    # Brief nudges: the Slack/email reminder sequence that drives users to open
    # their weekly brief (app/brief_nudge.py). OFF by default — no real user is
    # messaged until this is explicitly enabled. Day 0 sends inline at brief
    # generation; the cycle sweeps Day 1/2/3 reminders while a brief is unopened.
    brief_nudge_enabled: bool = False
    brief_nudge_interval_hours: int = 6
    ds_agent_url: str = ""  # e.g. http://localhost:8001

    # GitHub connector (GitHub App with user-to-server OAuth)
    github_app_id: str = ""
    github_app_client_id: str = ""
    github_app_client_secret: str = ""
    # Private key as a PEM string (-----BEGIN ... -----END ...). When stored in
    # .env, newlines should be literal \n; we normalize at load time.
    github_app_private_key: str = ""
    github_oauth_redirect_uri: str = ""
    github_webhook_secret: str = ""
    # GitHub App slug (the URL fragment in github.com/apps/<slug>). Used to
    # build the App install URL the user is redirected to after OAuth when
    # they have no existing install (so they can pick which repos to grant
    # the agent access to). Defaults to the production slug.
    github_app_slug: str = "sprntly-ai"

    # Design Agent share-token secret. A DISTINCT secret from
    # jwt_secret — never reuse JWT_SECRET for Design Agent surfaces. Now ALSO the
    # HMAC key for the bundle-proxy grant cookies (da_view_grant / da_share_grant).
    # No JWT_SECRET fallback. MUST be set in prod; the bundle proxy fails closed
    # (mint + validate both refuse) when this is empty — never serve with an
    # unsigned/forgeable grant.
    design_agent_token_secret: str = ""

    # Bundle-proxy public origin (Decision 2 — same-origin serving). The prototype
    # bundle is served from the APP origin (e.g. https://app.sprntly.ai) via an
    # nginx reverse-proxy to the FastAPI /v1/design-agent/.../bundle routes, under
    # the /_da-bundle/ prefix. This is a CONFIG-derived constant — the proxy base
    # baked into bundle_url is NEVER built from the inbound Host header (host-trust
    # guard, plan fix-item #4). Empty default falls back to frontend_url so local
    # dev / tests get a same-origin base without extra config.
    design_agent_bundle_origin: str = ""
    # The URL-path prefix the app-origin nginx maps to the FastAPI bundle routes.
    design_agent_bundle_path_prefix: str = "/_da-bundle"

    # Feature flag: set to true in .env to enable Design Agent routes.
    # Routes return 404 when false so the feature is invisible when off.
    design_agent_enabled: bool = False

    # Multi-Agent mode: run PRD + Evidence + Technical Design + QA Test Cases +
    # Risk Analysis + Traceability Matrix concurrently from a single trigger.
    # "aggressive" mode pulls ClickUp task context (comments, attachments,
    # linked tasks) into the generation context for deeper analysis.
    multi_agent_enabled: bool = True
    # Analysis depth: "standard" (PRD + Evidence + User Stories) or
    # "aggressive" (adds Technical Design, QA Test Cases, Risk/Gap Analysis,
    # Traceability Matrix, and ingests ClickUp task context).
    analysis_mode: str = "aggressive"
    # Max concurrent agent calls during multi-agent orchestration.
    multi_agent_concurrency: int = 6

    @property
    def github_app_private_key_pem(self) -> str:
        """Return a clean PEM regardless of how the .env value was written.

        Why this defensiveness: systemd's `EnvironmentFile=` parser handles
        unquoted values DIFFERENTLY from pydantic-settings + python-dotenv —
        in particular, an unquoted value with `\\n` sequences can have its
        backslashes silently stripped, leaving the running process with a
        PEM that has literal `n` characters where line breaks should be.
        PyJWT then raises `"Could not parse the provided public key."`

        Defensive normalisation steps (in order):
          1. Strip leading/trailing whitespace
          2. Strip a matched outer pair of single OR double quotes
          3. Replace any literal `\\n` with real `\n` (idempotent if the
             value is already a valid multi-line PEM)
        """
        raw = self.github_app_private_key or ""
        if not raw:
            return ""
        # Trim ONLY non-newline whitespace before quote detection. A clean
        # PEM ends with `\n` and we must preserve that; pyjwt accepts PEMs
        # without it too, but losing the trailing newline subtly changes
        # the value and can mask diffs in tests / logs.
        raw = raw.strip(" \t\r")
        # Strip a balanced outer pair of quotes (both must match).
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in '"\'':
            raw = raw[1:-1]
        # Idempotent: a PEM that already has real newlines has no `\n`
        # sequences to replace, so this is a no-op for that case.
        return raw.replace("\\n", "\n")

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def cookie_secure(self) -> bool:
        return self.env == "production"

    @property
    def design_agent_bundle_base(self) -> str:
        """Public base for the bundle proxy, host-trust safe (NEVER from Host).

        Shape: ``<origin><prefix>/v1/design-agent`` e.g.
        ``https://app.sprntly.ai/_da-bundle/v1/design-agent``. Callers append
        ``/{prototype_id}/bundle/index.html`` (authed) or
        ``/by-token/{token}/bundle/index.html`` (public/passcode). Falls back to
        ``frontend_url`` when ``design_agent_bundle_origin`` is unset so local dev
        and tests get a same-origin base without extra config.
        """
        origin = (self.design_agent_bundle_origin or self.frontend_url or "").rstrip("/")
        prefix = "/" + (self.design_agent_bundle_path_prefix or "").strip("/")
        if prefix == "/":
            prefix = ""
        return f"{origin}{prefix}/v1/design-agent"

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def template_path(self) -> Path:
        return Path(self.template_dir)


settings = Settings()

# Back-compat: existing code (corpus.py, etc.) imports DATA_DIR directly.
# Keep it as a module-level alias of settings.data_path.
DATA_DIR = settings.data_path
TEMPLATE_DIR = settings.template_path
