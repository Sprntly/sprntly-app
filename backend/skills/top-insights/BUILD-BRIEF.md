# Build brief — Top Insights

**Read this first. Everything else in this package is reference.**

You are building the plumbing around a skill that is already specified. The skill decides *what to show*; you build *where the inputs come from, where the data lives, and where the buttons go.*

This document is organised as: what it does → what you must build → what I need from you → what's still undecided.

---

## 1. What the thing does, in four sentences

A PM subscribes to some set of analysis categories and picks a cadence. On schedule, the skill reads the output of our existing analysis skills, merges and ranks everything it finds, and renders an HTML brief with three to five cards. Each card names a problem, sizes what's at stake, and links to the evidence. Anything the reader has already seen and that hasn't changed does not appear again.

The hard part is not the writing. It's the selection: five or six sources at five to ten findings each means **forty to sixty candidates competing for three to five slots**, every single run.

---

## 2. What you need to build — the checklist

| # | Thing | Why it's needed | Blocking? |
|---|---|---|---|
| 1 | **Settings UI** — 5 inputs (§3) | Nothing runs without config | Yes |
| 2 | **Adapters** — read each skill's saved output (§4) | The skill has no data otherwise | Yes |
| 3 | **State store** — ledger + backlog (§5) | Without it, every run repeats itself | Yes |
| 4 | **Two routes** — report view, evidence view (§6) | Both CTAs point at these | Yes |
| 5 | **Action events** — dismiss, defer, generate PRD (§7) | Otherwise the brief never advances | Yes |
| 6 | **Scheduler** — fire the skill on cadence | | Yes |
| 7 | **Backlog page** — ranked list of what didn't make it | The reader's escape hatch | No |
| 8 | **Delivery** — email, in-app, or both | | No |

---

## 3. The settings UI — five inputs

These are the only things we need from the user. Build a settings screen (or fold into onboarding) that captures them and writes them to config.

### 3.1 Categories — multi-select, at least one

```
☑ Top customer problems      ☑ Competitor & market moves
☐ Reliability & incidents    ☐ Your core metric
☐ Worth celebrating          ☑ What to build next
```

Three of these have no skill behind them yet (reliability, core metric, celebrate). Show them, let them be selected, and they'll simply contribute nothing until we build the source. **Do not** show an error or an empty state for them — an unwired category contributes silently.

### 3.2 Cadence — single select

`Daily` · `Weekly` · `Monthly`

This is the single highest-impact setting. It changes card budgets, greeting language, and how often a monthly report can produce a card. Store the literal value; the skill derives the interval.

### 3.3 Core business goal — three fields

```
Goal statement   [ Lift net revenue retention to 115%        ]
Metric           [ NRR ]      Target  [ 115% ]
```

Required if they subscribed to *What to build next* or *Your core metric*. If it's empty, those categories emit nothing rather than guessing — so mark it required when either is selected.

### 3.4 Prioritization criteria — one free-text field

```
What should we prioritise for you?
[ I care most about enterprise churn risk and anything touching     ]
[ activation. Don't show me anything under $250K. This quarter      ]
[ I'm focused on onboarding.                                        ]
```

**This is the input that needs the most thought on your side.** The user writes a sentence or three. The skill compiles it into three structured things: hard filters (exclusions), weighted preferences (reordering), and a focus (this quarter's emphasis).

Two requirements:

- **Show the compiled version back and make them confirm it.** "Got it — I'll exclude anything under $250K, rank enterprise churn higher, and lean toward onboarding this quarter." They must always be able to see what their sentence became, because it's driving what they do and don't get told.
- **Version it.** Every edit increments a version number, and each brief records which version produced it. A reader asking "why did last month's brief look like that" needs an answer.

Whether the compiled filters are directly editable is your call. I'd start with statement-only and add direct editing if people ask.

### 3.5 Company scale — one field

```
Annual revenue  [ $120M ]
```

Used to normalize impact. $2.2M is a five-alarm fire at one company and a rounding error at another, and without this the ranking treats them identically. Approximate is fine.

### Optional: delivery time

If briefs go out by email, what time. Not needed for v1.

---

## 4. Adapters — where the data comes from

One adapter per category. **An adapter reads a saved output and reshapes it. It never analyses, never computes, never fills in a missing number.** If a figure isn't in the upstream output, it doesn't exist.

Every adapter returns this:

```json
{
  "category": "competitive",
  "source_skill": "competitive-intelligence-review",
  "as_of": "2026-07-01T09:00:00Z",
  "refresh_interval_days": 30,
  "available": true,
  "report": {
    "ref": "https://…/reports/ci-2026-07",
    "summary": "Two rivals moved on pricing this month…",
    "ran_at": "2026-07-01"
  },
  "findings": [ … ]
}
```

Four things about this that matter more than they look:

- **`as_of` is when the analysis *ran*, not when you fetched it.** Every freshness decision depends on this being right. Getting it wrong makes month-old content look new.
- **`available: false`** means the source isn't wired or is unreachable. The category then contributes nothing *and the brief says nothing about it.* An empty `findings` array with `available: true` is different — that means the source ran and found nothing, which is a real result.
- **Return every finding in a report, not just the headline.** Reports get broken up and released across the month. If the adapter only hands over the top one, the category goes silent for four weeks.
- **`report.summary` is verbatim from the report.** Never write a fresh one. Summarising a report you didn't read is how a confident, wrong sentence reaches a PM.

`references/signal-schema.json` has the full `finding` shape. The fields that carry the most weight: `id` (must be stable — see §9), `pain`, `value` with its `basis`, `evidence`, `confidence`, `urgency`, `as_of`.

---

## 5. State — two things to persist

Neither exists today. Both are small.

**Ledger** — one row per finding ever surfaced. Finding id, when it was last shown, how many times, a snapshot of its numbers at that moment, and what the user did about it. This is what makes "don't show me the same thing twice" possible; the snapshot is what "materially changed" is measured against.

**Backlog** — everything that didn't make this brief, ranked, each with a reason. Feeds the backlog page and lets the next run promote the next item when something is dismissed or resolved.

Scoped per reader. Both are written at the end of every run and read at the start of the next.

---

## 6. Two routes the CTAs need

Every card has exactly two buttons. Primary goes to evidence, ghost goes to the PRD.

| Route | What it shows | Used by |
|---|---|---|
| `/reports/{id}` | The full analysis report. **Opens in a new tab.** | Cards from a report, and every shelf tile |
| `/evidence/{finding_id}` | What this finding rests on — the tickets, the datapoints, the interview quotes | Every other card |

The evidence view is the one that carries the product's new posture. We are telling PMs *here's what we found and here's why we believe it*, not *here's the fix, approve it*. If there's nowhere to go when they click "View the evidence," that promise doesn't land.

If you need to ship before the evidence view exists, the fallback is pointing report-backed cards at their report and leaving PRD as primary on the rest. Treat that as temporary.

---

## 7. Actions the UI must send back

Three, and they mean different things. Conflating them is how this becomes annoying.

| Action | UI | Means | We do |
|---|---|---|---|
| **Dismiss** | × on the card | Not interested | Suppress unless it gets materially worse |
| **Defer** | "Not now" | Interested, wrong moment | Hide for one refresh interval, then return at full rank |
| **Generate PRD** | Ghost CTA | Taken up | Mark in progress, stop surfacing, free the slot |

Deferral is the one most likely to get cut for scope. Don't. Without it, "not this week" and "never" are the same button, and people stop dismissing anything for fear of losing it.

**Dismissal is not live-replace.** The card greys out with an Undo, exactly as it does today. The replacement arrives in the next brief. A card sliding in underneath a dismissal makes the thing feel like an inbox that never empties.

Also useful if cheap: PRD *approved* (distinct from generated), and a fired-on-resolve event so we can move a finding to "worth celebrating."

---

## 8. The prioritization framework — fully specified

You don't need to design this; it's decided. Included so you can implement or sanity-check it.

Two stages. **The gate** is objective — is this finding sound? Same answer for every reader. **The profile** is subjective — does this reader want it? Only the second is overridable by the user.

```
base = 0.32·impact_norm + 0.20·confidence + 0.18·urgency
     + 0.12·reach_norm  + 0.18·freshness

adjusted = base × Π(preference_multipliers) × focus_multiplier
```

- `impact_norm` — value at stake, normalized against company scale (§3.5)
- `freshness` — 1.0 never seen, 0.6 changed since last seen, 0.2 promoted to fill an empty brief
- preference multipliers clamped to **0.5–2.0**. Anything wider is a filter pretending to be a preference, and belongs in hard filters where the user can see it

**Card budgets:** daily 0–5 (default 3), weekly 0–7 (default 5), monthly 0–7 (default 6). Max two per category. These are ceilings, not quotas — two cards is a correct brief, and so is zero.

**Two rules that will look like bugs and aren't:**

1. **A quiet day produces a one-line greeting and no cards.** That's correct. Don't add a "nothing to show" empty state that apologises.
2. **A maximum-severity finding ignores the user's filters entirely** and says so on the card. Someone who asked for "only growth work" still gets told about a data breach. This should fire a handful of times a year; if it's weekly, the severity bar is wrong.

**One thing we deliberately did not build:** the system does not learn from behaviour. It would be easy to quietly down-weight whatever people dismiss — and it would break the product. A digest that learns to show you only what you already engage with stops being able to tell you what you don't know, which is the entire reason it exists. Behaviour may *propose* a change ("you've passed on three onboarding findings — want to down-weight them?"); the user accepts or declines. It never adjusts silently.

---

## 9. What I need from you — the blanks

Ordered by how much they block.

### Blocking

**1. Do our skills produce stable finding IDs across runs?**
The single most important question here. The ledger keys off finding ids. If voice-of-customer generates fresh ids every week, then nothing can be recognised as "already seen" — no freshness, no dismissal memory, no rotation, no resolution tracking. Every anti-repetition mechanism in this spec is inert. **Check this before anything else.** If ids aren't stable, making them stable is the first ticket.

**2. Are report findings separately addressable, or is the report just prose?**
Reports get decomposed and released across their interval. If findings only exist as paragraphs inside a document, we need either a parsing step or a change upstream to emit them as structured items. Without it, the competitive category produces one card and then nothing for four weeks.

**3. Two skill names are ambiguous.**
Confirmed: `competitive-intelligence-review`, `interview-synthesis`.
- Internal channel: `voice-of-customer-report` or `voc-volume-severity`? The second sounds like a prioritisation stage rather than a source — if it runs downstream, we probably want *its* output, since volume and severity are exactly the fields we need.
- Public channel: `feedback-synthesis`, `third-party-feedback`, or `public-feedback-report`? Pick based on which one reads external surfaces. **If two of them read Reddit, we have a problem** — the same complaint arrives twice and gets counted as two independent sources agreeing, inflating confidence on a finding that only had one source.

**4. Where does each skill save its output, in what shape?**
Path, table, or API. **One real sample output from any single skill unblocks more than anything else on this list** — I can replace the invented field mapping with a real one and tell you within minutes whether ids are stable, whether findings are addressable, and whether figures come with a basis attached.

**5. Where do the ledger and backlog live?**

**6. Do the report and evidence routes exist, or do they need building?**

### Needed soon, not blocking

7. Can we emit an event when a PRD is generated or approved? That's what moves a finding to "in progress" and frees the slot.
8. Does the competitive skill emit anything between monthly reports? Interim findings can card immediately; report content is paced.
9. Where does the prioritization criteria get captured, and do we compile it server-side or in the skill?
10. Is there a default profile for someone who writes nothing? Right now the fallback is the base formula — sensible but generic.

### Not needed yet

11. Reliability, core metric, and ship-impact skills. All three are written as contracts that return empty. The skill runs correctly without them and lights up when they land. **Celebrate already has a working feed** with no new skill: when a finding the reader acted on gets resolved, it becomes a celebration automatically.

---

## 10. How to know it works

**The seven-day test.** Run seven consecutive daily briefs against a fixed set of data where only one source refreshes. Correct behaviour: the briefs get progressively quieter, and the monthly competitive report's findings appear spread across the week rather than all on day one. If you get five cards every day for a week, the freshness logic isn't working — regardless of how good each card reads on its own.

That test catches more than any other. The failure mode here is not ugly output; it's *plausible* output that quietly repeats itself, and it only shows up over consecutive runs.

**Other things worth checking:**
- Every number in a brief traces back to a field in some skill's output. Any orphan number is a bug.
- Dismiss something, run again — a different card is in its place.
- Defer something, run again — it's gone. Wait one refresh interval — it's back, at full rank.
- Set a filter that would exclude a critical finding, then feed in a critical finding. It should appear anyway, and say why.

---

## 11. Files in this package

| File | What it is |
|---|---|
| `BUILD-BRIEF.md` | This document |
| `SKILL.md` | The full specification. Authoritative — if this and the brief disagree, SKILL.md wins |
| `README.md` | Orientation for whoever runs the skill |
| `references/sources.md` | Adapter contracts, per-source detail, open items |
| `references/signal-schema.json` | Every data structure, fully typed |
| `references/rubric.md` | Quality checks, including the ones worth running as code |
| `references/examples.md` | Worked examples and twenty annotated failure modes |
| `assets/brief-template.html` | The render template. Differs from the current weekly brief by three CSS variables and the report shelf |
| `top-insights-brief-format.html` | A rendered specimen — open this first to see what we're building |

**Start with the specimen.** It's a working brief with real-looking content; it'll tell you more in thirty seconds than this document does in ten minutes.
