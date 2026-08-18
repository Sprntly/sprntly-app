# Ask Planner — implementation brief

**Status (2026-08-07):** SHIPPED as the front door. The planner decides every
chat message — action, method and sources — and the regex cascades it replaced
are deleted, in `qa_agent.answer` and in all three client dispatchers. Shadow
mode is superseded; the slice table in §7 is kept below as the record of how it
got here, not as work outstanding.

**Two corrections to what this brief originally claimed**, both found by reading
the code rather than the doc:

1. §3 says "`qa_agent.answer` is Slack's front door." **There is no Slack chat
   surface.** Slack appears only as a CONNECTOR (`slack_oauth.py`,
   `routes/connectors.py`) — a source we read from. No route, no events handler,
   no slash-command handler. Nothing about placement followed from it.
2. §6 says live connector reads "carry constraints in the tool args, so the
   connector half works without this." True of exactly ONE adapter: only
   `slack_search_messages` accepts a window (`days`). `jira_search`,
   `confluence_search`, `clickup_search_tasks` and `hubspot_search` take
   keywords and filters and no date range. `app/live_read.py` applies `top_n`
   itself and RECORDS the constraints an adapter cannot express rather than
   discarding them silently.

**Author:** research pass over `qa_agent` / `skill_router` / `connector_lookup`, 2026-08-03.

A single LLM planner decides, per chat message, *what to gather* before the
answer runs: which pipeline or company skill applies, which connectors to read
live, whether the knowledge graph rides along, whether to search the web, and
under what constraints. It returns one JSON envelope; the app executes it.

This supersedes nothing about the *router's* correctness — the routing ladder is
fine. What is missing is a planning step that selects **sources** by meaning
instead of by the user naming them.

---

## 1. Baseline — what already runs today

Three LLM calls already fire per web chat message:

| Call | Model | Where | Version |
|---|---|---|---|
| Intent envelope | Sonnet, `max_tokens=4000` | `app/chat_intent.py:253` | `chat-intent-v1` |
| Skill/pipeline route | Haiku, `temperature=0`, `max_tokens=300` | `app/qa_agent.py:592` | `qa-router-v7` |
| Answer | Sonnet (→ Opus on heavy) | `ask_runner.compose_ask_answer` | — |

Routing is therefore **already AI-decided** for any phrasing the regex tier
misses. "There is no AI in routing" is not an accurate description of the system.

### `qa_agent.answer` has TEN deterministic interceptions

In order, each returning before `route()` is ever consulted:

1. `call_index.is_listing_request` — "give me the 5 latest transcripts"
2. `call_index.is_single_call_request` — "summarize the Globex Partners call"
3. `is_call_digest` — live fetch + VoC over a call window
4. `is_voc_report_request` — bare "VoC report" (gated on a connected call source)
5. `is_data_analysis_request` — DS engine over uploaded tables
6. `call_index.windowed_call_question` — index-driven, asks the data not the vocabulary
7. `is_ticket_update` — rewrite a ticket from a PRD
8. `is_jira_lookup` — live tracker read (Jira, else ClickUp)
9. `is_connector_lookup` — live read of a **named** tool
10. `document_lookup_candidates` — document question naming **no** tool

Each carries an incident in its comment, and their precedence is pinned by ~20
assertions in `backend/tests/test_connector_lookup_routing.py`. Ordering is
load-bearing — several sit where they do specifically to avoid hijacking each
other (e.g. #7 above #8; #6 below #5).

### The connector execution layer already does the hard part

`app/connector_lookup/` is further along than the proposal assumes:

- **Multi-provider tool loop already exists.** `answer.py:288` takes
  `providers: list[LookupProvider]`, builds a combined tool list, and runs it
  with `max_iters` + a deadline.
- **The KG is just another reader.** `include_knowledge_graph=True` adds it to
  the same loop (`connector_lookup/knowledge_graph.py`).
- **Availability is already resolvable.** `registry.connected_providers(enterprise_id)`
  returns what the company actually has wired (Slack counted separately — it is
  per-user).
- **Honesty tiers already exist.** `LOOKUP_PROVIDERS` (8 readable: jira, clickup,
  slack, fireflies, github, hubspot, google_drive, confluence) /
  `DEFERRED` (4: syncs to KG, no live adapter) / `NO_CONNECTOR` (10: not a
  Sprntly connector at all). A question about Zendesk gets an honest answer
  rather than a fabricated read.
- **Cap:** `MAX_PROVIDERS_PER_LOOKUP = 2` (`registry.py:130`).

**So fan-out, honesty, and availability all exist. Only the decision is missing.**

---

## 2. What the planner actually changes

Exactly three things:

1. **Replaces the name-match gates.** `skill_router.is_connector_lookup:1094`
   fires only when the user literally names a tool ("Routing is
   explicit-name-only for now" — `connector_lookup/slack.py:27`).
   `document_lookup_candidates` is a broader keyword net for the unnamed case.
   Both become planner output.
2. **Raises `MAX_PROVIDERS_PER_LOOKUP`.**
3. **Emits constraints** (window, top-N, entity) — which currently have nowhere
   to land on the KG path. See §6.

---

## 3. Where it hooks — REVERSED BY OWNER DECISION (2026-08-03)

The original recommendation — planner replaces `route()` only, interceptors
stay ahead — is preserved below for the record, but the owner ruled the other
way: **"the planner should be the first thing, and the planner tells us the
remaining things what to do."** The planner is the front door; the interceptors
are demoted to machinery it can name. The costs the original analysis flagged
(a planner call on every message; re-deciding paths that exist for measured
cost reasons — the call digest comment records ~168s and ~$0.23 for a question
the call index answers from one Postgres query) were stated and accepted.

Current implementation of that ruling (shadow slice): the dispatch sits at the
TOP of `qa_agent.answer`, before every interceptor, and the planner's menu
gained the machinery vocabulary — `call-digest`, `call-listing`,
`single-call-read`, `data-analysis`, `tracker-lookup`, `ticket-update`
(`_MACHINERY_IDS`, prompt `ask-planner-v2`) — so an intercepted turn can score
as agreement rather than always reading as a miss. The ladder still answers;
comparison is offline — the plan lines join the runner's
`ask-planner actual:` line on `question`. Slash and pinned turns never shadow.

<details>
<summary>Original (superseded) placement analysis</summary>

**Do NOT place the planner in front of everything.** That makes it an eleventh
tier, adds a fourth LLM round-trip before any answer, and invalidates the pinned
precedence assertions.

**Place it where the haiku router is.** The planner *becomes* `route()`,
absorbing interceptions #9 and #10, and leaves #1–#8 deterministic.

Rationale: #1–#8 own specific phrasings for measured cost reasons. Those must
not be re-decided by a model.

Consequence: round-trip count stays flat (planner replaces haiku, not adds to
it), the expensive fast paths keep working, and the planner gains the one
capability it needs — choosing sources semantically.

</details>

**It lives in BOTH, and that turned out to be the point.** The original note
here justified `qa_agent` over `chat_intent` on the grounds that the former was
"Slack's front door" — which is not true (see the status block above). The real
reason the two are different is narrower: `chat_intent` decided the ACTION and
`qa_agent.route` decided the ROUTE, as two model calls that could not see each
other. The planner is now behind both, so it is one decision and one call —
strictly fewer round trips than what it replaced, not more.

---

## 4. Output envelope

Fields:

| Field | Purpose |
|---|---|
| `pipeline_id`, `confidence` | Absorbs today's `skill_id` — one of the four pipelines |
| `company_skill_id`, `company_confidence` | **Separate field, deliberately.** See below |
| `sources[]` | Provider keys to read live |
| `include_knowledge_graph` | Whether the KG rides in the same tool loop |
| `web_search` | Whether to sweep the public web first |
| `constraints` | `since` / `until` / `top_n` / `entity` |
| `in_scope` | Scope gate; check with strict `is False` so a malformed field fails open |

**Custom-skill precedence stays a separate field gated in Python**, per the
existing rule at `qa_agent.py:641-651`: *"Asking the model to prefer company
skills makes precedence a model preference nobody can assert in CI; checking the
separate field here makes it a property of the code, provable with a stubbed
call."*

**Model proposes, Python disposes.** `sources[]` must be intersected with
`connected_providers()` in code before execution. Trusting the model's list means
it will confidently name a provider the company does not have.

---

## 5. Traps

**Prompt cache + cross-tenant leakage.** `_ROUTER_SYSTEM` is tenant-invariant and
cache-controlled. The company's connector list must ride the **uncached `input`**,
exactly as `_custom_skill_block` does. Putting per-company data in the cached
prefix leaks names across tenants and forks the prompt cache — the same trap
CLAUDE.md documents for the skills menu.

**Do not destroy the zero-LLM path.** The regex tier is *terminal* when a company
has uploaded no custom skills (`qa_agent.py:566-569`) — zero LLM calls for
routing, which is what most companies get. An unconditional planner makes every
company pay for every message.

**Per-message cost rises.** Replacing haiku-at-300-tokens with a call reasoning
over the connector catalog + live connections + skill library is a materially
bigger prompt on every turn. Shadow-measure before committing.

**Tool-list size.** Each provider contributes a toolset to the loop. The ≤2 cap
is not arbitrary — Anthropic documents tool-selection accuracy degrading past
~30–50 tools. Raise to 3–4, not unlimited.

**Fail open.** `route()` wraps the classifier in try/except and falls back to the
regex hit; the planner must do the same, falling back to today's `route()`.

**The planner judges `_routing_text`, not `question`** (added by #1034,
`b4ad698a`, merged after this brief was first written). An attached document's
own vocabulary must never decide routing — a comparison doc mentioning "board"
and "ticket" once each was enough to hijack a turn to the tracker path. So:

- Every interceptor and `route()` now judge `_routing_text(question)` — the text
  up to the first `[Attached files]` marker, i.e. only what the user typed.
- `route()`'s input alone gets `_routing_text_with_filenames(...)`, which appends
  attached/uploaded **filenames only, never content**, so a document question is
  still recognisable as in-scope.
- That value is a **new local, never assigned back** over `routing_text`. A
  filename like "Sprint Planning Board.docx" carries the same tracker nouns an
  attachment body does; leaking it upward to the interceptors reopens the bug
  through a different door.

The planner sits exactly where `route()` sits, so it inherits both: it plans on
`_routing_text_with_filenames`, and the full `question` still reaches
grounding/answering/persistence unchanged.

**Capability preconditions are now the house style** (same commit). The tracker
and DS interceptors used to claim a turn on a lexical match alone and then answer
with a canned refusal for a capability they never had ("connect Jira") — they now
require the capability to exist before claiming the turn, and a declined
precondition falls through to normal routing. This is direct precedent for the
planner rule that `sources[]` must be intersected with `connected_providers()`:
never plan for a source the company does not have.

---

## 6. The blocked half

`graph/retrieval.py:185` — `retrieve_context(facade, enterprise_id, question, *,
k, token_budget)`. No `since`, no `until`, no `top_n`, no entity.

A planner emitting `{"window": "last month", "top_n": 5}` hands those to a
function that cannot receive them; they are silently dropped.

Live connector reads carry constraints in the **tool args**, so the connector
half works without this. The KG half needs slices 1–3 of the connector-constraint
plan (root cause: `Signal.valid_at` stores extraction wall-clock time, not when
the thing happened). Shipping the planner alone yields correct *source selection*
with still-wrong *date arithmetic* on anything KG-grounded.

---

## 7. PR slicing

| # | Branch | What | Notes |
|---|---|---|---|
| 1 | `feat/ask/planner-shadow-mode` | Planner module + schema; runs alongside `route()`, logs to `agent_decision_log`, acts on nothing | **Built** (on `feat/ask-planner`). Two journal lines per shadowed message: `ask-planner raw:` (the model's ungated response + the clamped question) and `ask-planner shadow:` (the gated plan vs. the router's decision, with `agree` keyed on DESTINATION only and `same_tier` reported separately — tier equality would score every regex-terminal turn as a false disagreement). Slash turns are not shadowed (nothing to measure; billed to the customer's key). Known accepted trade-off: one extra uncached `feature_flags` read per message on the shadow's own daemon thread, and one bare thread per message rather than a pool — both invisible to answer latency, both worth revisiting if enrolment grows past a pilot |
| 2 | `feat/ask/planner-decides-the-route` | Planner replaces the haiku router. No connector change | Round trips stay flat |
| 3 | `feat/ask/planner-picks-the-sources` | Planner emits `sources[]`, replaces `is_connector_lookup` | **Done.** Executed by `app/live_read.py` |
| 4 | `feat/ask/read-more-than-two-tools-at-once` | Cap → 3–4, parameterized | **Superseded — the cap is GONE.** `MAX_PROVIDERS_PER_LOOKUP` bounds the serial TOOL LOOP, where accuracy degrades past ~30–50 tools. `live_read` is a parallel fan-out with the model not in the loop, so its costs are wall clock (one shared deadline: breadth costs the slowest source, not the sum) and prompt characters. Both are bounded in the executor, so a question that genuinely spans every connected tool may name every connected tool |
| 5 | `feat/ask/planner-can-call-for-a-web-search` | `web_search` action | |
| 6 | *blocked* | Constraints honoured end-to-end | Needs KG constraint slices 1–3 first |

Slices 1–3 are the minimum that delivers the proposal. **Ship 1 alone and read
the shadow data before writing 2.**

### Test files that gate this

- `backend/tests/test_connector_lookup_routing.py` (~20 precedence assertions)
- `backend/tests/test_qa_router_evals.py`
- `backend/tests/test_ask_skill_routing.py`
- `backend/tests/test_connector_lookup_document_intent.py`
- `backend/tests/test_chat_intent_evals.py`

---

## 8. Relationship to the 2026-08-01 finding

Two independent research passes rejected inverting the routing ladder to
LLM-first. That finding stands and is **not** what this proposes. The difference:

- **Rejected:** replacing the regex cascade with an LLM/agentic router, i.e.
  making the same *routing* decision a different way. Rejected because the
  questions that fail already reach the haiku classifier — routing was never the
  defect.
- **This:** a planner that decides *what to gather* — a decision no layer makes
  today at all. The router picks exactly one destination; nothing chooses a
  source set, a web sweep, and a skill together.

The constraints from that finding still bind: no new pre-router tier (§3 keeps
the count flat), no wider regexes, and the ten interceptions in `qa_agent.answer`
must not be reordered.
