# Brief Nudge — skill folder README

> **Read this first.** This folder is a self-contained Agent Skill. It contains everything needed to understand, invoke, and reproduce a brief nudge: the behavior spec (`SKILL.md`), this guide, and the rendered examples (`examples/`). An LLM or a developer can pick it up with no other context and know **what the skill is, when to call it, what it needs, what it outputs, and what that output is for.**

## 1. What this skill is (one line)
`brief-nudge` writes the multi-channel notification + reminder sequence — **Slack and email**, a **day-0 announcement** plus **day-1/2/3 reminders** (sent only while the brief is unopened) — that drives a user to open a brief such as `weekly-brief`, each message leading with the **business impact** and one **deep-link CTA**.

## 2. What it produces
For each **channel × day**, a ready-to-send message:
- **Slack** — compact: a bold, impact-led headline, a short teaser of the top items, and **one** button. No subject.
- **Email** — titled and branded: an impact-led **subject + preheader**, a **title** that names the figure, a hero line, a scannable "what we're seeing" list (each item with its dollar impact), **one** big CTA, and a footer (workspace, manage-notifications, unsubscribe).

One nudge = one brief = one primary action.

## 3. What the output is used for
This is the **delivery / activation layer** that sits *after* a brief is generated. It does not write the brief (`weekly-brief`) or run analysis — it gets the brief opened:
- announce the brief the moment it's ready;
- recover attention with honest, escalating reminders while it's unopened;
- deep-link the reader straight to the **right workspace → the brief page**, so the CTA lands them exactly where they can act.

```
weekly-brief  ->  brief-nudge  ->  user opens the brief and acts
  (the artifact)   (THIS SKILL)      (Slack + email, day 0..3)
```

## 4. When to call it / when NOT to
**Call it when** a brief (or any surfaced artifact) needs opens, and you're delivering over Slack + email with a day-based cadence.
Literal triggers: "notify users about the brief", "reminder sequence", "nudge users to open X", "Slack + email announcement", "drip reminders".

**Do not call it** to write the brief (`weekly-brief`), to brief leadership on an external event (`market-event-brief`), or for incident / general customer comms (`customer-comms`).

## 5. Inputs
| Field | Required | Notes |
|---|---|---|
| `brief_ref` | required | workspace + brief id/URL, and the deep-link target (workspace → brief page) |
| `rollup` | required | the brief's headline figure (total upside) |
| `top_items[]` | required | highest-impact items, each `{label, what_we_see, impact}` |
| `recipient`, `close_date`, `per_item_links`, `brand`, `open_state` | optional | strengthen or gate the sequence; omit if absent |

**Hard rule:** every figure traces to the brief. Never invent numbers; never manufacture urgency.

## 6. How to use it — the loop
1. **Read `SKILL.md` in full.** It is the contract: select items → one CTA → day 0 → reminders → render → honesty pass.
2. **Pull the rollup + top items** from the brief; pick the 1–3 with the highest impact.
3. **Lead with the figure** — every email subject, email title, and Slack headline opens with the concrete business impact.
4. **Compose the cadence:** Day 0 announce (top 3) → Day 1 impact-led (top 2) → Day 2 focused (the single biggest item + the cost of waiting) → Day 3 final (one item + close date + an honest "we'll pause reminders").
5. **Render per channel** (Slack compact, one button / email titled and scannable), holding layout constant so every message reads the same way.
6. **Honesty pass:** figures trace to the brief; urgency comes from **time + cost-of-waiting**, not repetition; the final reminder keeps its promise to stop.

## 7. Cadence × channel (quick reference)
| | Day 0 — announce | Day 1 | Day 2 | Day 3 — final |
|---|---|---|---|---|
| **Slack** | rollup + top 3 + button | rollup + top 2 | biggest item + cost of waiting | one item + close date + pause note |
| **Email** | subject + title + hero + 3 items + CTA | "still on the table" + top 2 + CTA | focused subject + one item + CTA | "final reminder" + one item + pause note + CTA |

## 8. Output contract
- **Slack:** `{ headline, intro, items[], primaryCTA(deeplink) }` — one button; deep link attached.
- **Email:** `{ subject, preheader, eyebrow, title, intro, items[], primaryCTA(deeplink), perItemLinks?(secondary), footer }`.
- The CTA always routes to **workspace → brief page**. Day 3 includes the close date and the pause note.

## 9. Files in this skill
```
brief-nudge/
├── SKILL.md                 # authoritative behavior spec — read first
├── README.md                # this guide
└── examples/
    ├── preview.html         # rendered gallery of all 8 messages (open in a browser; switch days)
    └── messages.md          # the full copy of every message — read to copy the pattern
```

## 10. Guardrails (do not violate)
- **One dominant CTA** per message; per-item links are optional and never compete with the button.
- **Impact-led** subjects, email titles, and Slack headlines.
- **Honest escalation:** Day 1→3 build urgency through time + cost-of-waiting, not repetition; no manufactured scarcity; the final reminder pauses.
- The CTA **deep-links** to the right workspace and the brief page — never a generic home.
- Reminders fire **only while the brief is unopened**.
