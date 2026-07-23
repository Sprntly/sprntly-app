# How the filtering works — in plain terms

*A walkthrough of what happens between raw customer input and a finished Voice of Customer report. Written to be readable by a person and implementable by an agent.*

---

## The short version

Feedback goes through **five gates** before it can appear in a report. Each gate answers one question, and each one throws things away for a stated reason.

```
 1,000 raw items from every source you have
        │
   ①  Is this even a customer talking?          →  drop internal chatter with no named account
        │
   ②  Is this something product can act on?     →  drop logistics, recruiting, HR, spam
        │
   ③  Who is actually making this claim?        →  drop reps speaking for imagined customers
        │
   ④  Does it fall inside what you asked for?   →  drop out-of-window, out-of-source, out-of-segment
        │
   ⑤  Does it move a goal you actually track?   →  keep, but don't recommend
        │
   ~500 records → ~6 themes → ~4 recommendations
```

The important property: **nothing is deleted silently.** Every gate records why it dropped something, and the report tells you how many were dropped and for what reason. An unexplained omission can't be reviewed or corrected; a disclosed one can.

---

## Gate 1 — Is this a customer talking, or us talking?

The first question is deceptively hard, because file names lie. A calendar invite says "Acme QBR" and it turns out to be three of our own people discussing Acme. Another says "Sync" and it's a two-hour customer discovery call.

So we ignore the metadata and **read the content**. The tells:

**Sounds like a customer:** someone says *"your tool"* or *"you guys"*. People introduce themselves. One side is explaining the product and the other is describing using it. Someone is being asked discovery questions.

**Sounds like us:** nobody ever says "your" — everyone talks like an owner. People use shorthand for internal systems without explaining it. No introductions. The conversation is about roadmap, staffing or process rather than about using the thing.

These are indicators, not a checklist, and the judgment is written down with a one-line reason so it can be argued with later. **When it's genuinely unclear, we mark it undetermined rather than guessing** — and undetermined material doesn't get counted as customer evidence.

**The exception that matters:** an internal conversation can still be excellent evidence if it names an account. "Acme told me SSO is blocking them" is real evidence about Acme, relayed through a colleague. We keep it and **attribute it to Acme, not to the colleague.**

## Gate 2 — Is this something product could act on?

**In:** feature requests, bugs, usability friction, performance and reliability problems, integration gaps, things people couldn't find that already exist, unclear documentation, and workarounds people built to compensate.

**Out:** scheduling, recruiting, vendor pitches, board discussions, all-hands, bot tickets, autoresponders, spam, and test accounts.

**Out before we even read it:** HR matters, compensation, privileged legal material, live security incidents, personal or payment data, material non-public information, and any account contractually barred from cross-account analysis.

Two subtleties do real work here:

**Workarounds are the most valuable thing on this list.** When someone has built a spreadsheet, a script, or a manual Monday-morning process to compensate for something, that's revealed behavior rather than stated preference — and *the workaround usually describes the real requirement better than the feature request does.* In the Marlowe example, a paralegal keeping a private index of what clauses are "actually called" tells you more precisely what's wrong with search than any of the tickets do.

**"I couldn't find it" is usually not a missing feature.** It's a discoverability problem, and we tag it as one. Getting this wrong is the single most common way a roadmap fills up with things that already exist. The Marlowe report calls this out explicitly: the clause library exists and is well populated in all twelve affected accounts — building a new one would have been an expensive way to miss the point.

## Gate 3 — Who is actually making this claim?

This is the gate that does the most quiet damage if you skip it. Everything gets an **origin tier**:

| Tier | What it is | Counts? |
|---|---|---|
| **Direct** | The affected user, describing their own experience, unprompted | ✅ |
| **Relayed** | Someone reporting a named account's words — including internal notes | ✅ credited to the account |
| **Elicited** | The user agreeing to a leading question we asked | ⚠️ counted, flagged |
| **Asserted** | One of our own people claiming *"customers keep asking for this"* with no named source | ❌ |
| **Speculative** | Anyone guessing about future needs | ❌ |
| **Undetermined** | Can't tell from the artifact | ❌ |

**Asserted and elicited are the two that masquerade as customer evidence.** An asserted claim is a rep speaking on behalf of customers who may not exist. An elicited one is demand manufactured by the question that produced it — ask "would per-team cost breakdowns be useful?" and almost everyone says yes; that tells you nothing about whether anyone would have raised it unprompted.

Both get captured. Neither gets to inflate a count. In the Cassette example this removed 117 records of 634 — 61 asserted, 34 elicited from renewal calls, 22 speculative — and the report says so on the page rather than quietly reporting 634 as though it were customer voice.

**A theme that survives only on asserted records is still worth reporting** — as an internal belief with no customer evidence behind it. That's a finding, not a gap.

## Gate 4 — Does it fall inside what was asked for?

If the request carried a filter, it's applied here and **every percentage is recomputed inside it.**

- **Window** — "last week", "last quarter", "March 1 to June 30". If no window is given, the default is the last full quarter, and the report says which default it picked.
- **Source** — "just the CSM calls", "only support tickets".
- **Segment or account** — "only enterprise", "just Northwind", "only churned accounts".

Two rules keep this honest:

**Never carry numbers across a filter.** If a theme affected 40% of all accounts but 63% of enterprise accounts, an enterprise-filtered report says 63%. Reusing the unfiltered figure would be wrong in a way that's almost impossible to catch downstream.

**Name what the filter excluded.** The Marlowe run states on the page that it left out 22 call transcripts, 2 exit interviews, 88 NPS verbatims and every non-enterprise ticket. That's the difference between a deliberate slice and an accidentally thin one — and a reader can ask for them to be folded back in.

**A filter can also cost you a whole column.** Marlowe's run is support tickets only, which carry no account status or ARR, so the churn and dollars columns are empty. The report says explicitly that this is a scope consequence and not a finding — *"nothing here should be read as no commercial risk."* An empty column that isn't explained will be misread as a zero.

## Gate 5 — Does it move a goal you actually track?

The last gate doesn't remove anything from the report. It decides what gets **recommended**.

Everything that made it through the first four gates appears in the report — the at-a-glance table, the radar, the theme cards, the long tail. But only about five things become recommendations, and they're chosen by **how much they move the metrics the team actually tracks**, not by how many people complained or how loudly.

That produces two moves worth understanding:

**Something wide and loud gets held back.** Kindling's Slack/Teams theme reached 13 accounts — the second-widest in the report — with no churn, no dollars and a frustration score of 2 out of 5. Customers believe it costs them participation; nobody can measure that. It's named in a *deliberately not recommended* block and routed to an experiment instead of a roadmap slot.

**Something quiet and small gets elevated.** Kindling's international catalogue theme is the calmest of the five and third by volume, and it's recommendation #3 — because it gates $340K of named expansion, which is a thing neither the volume axis nor the frustration axis can see.

**Something isn't ours at all.** Cassette's per-team cost attribution reached 45% of responding accounts. It's a billing-transparency question, not a product capability, so it's routed to pricing rather than promoted. The owner tag assigned back in capture is what makes this call available.

The *deliberately not recommended* block is required, not optional. It's the only thing that makes this gate auditable — without it, a reader can't tell the difference between "you considered this and passed" and "you missed it."

---

## What this costs, honestly

**Frustration scoring is judgment.** It's read from observable language — escalation words, blame or cancellation framing, repeat contacts, whether someone built a workaround, whether they describe having given up. It's grounded, but two careful readers might differ by a point, and the reports say so.

**Churn links are correlation.** An account that described a theme on its way out is not proof the theme caused the exit. Flagged as correlation every time; an experiment or a save-motion settles it.

**Survivorship is unfixable here.** Customers who left over something before the window opened aren't in the corpus. Nothing in this pipeline recovers them.

**Tickets skew toward breakage.** Things that are merely missing generate fewer tickets than things that fail. A ticket-only run — like Marlowe's — under-represents absence and over-represents defects. The report flags it; it doesn't fix it.

**Narrow scopes are snapshots.** A one-week or single-account run is a moment, not a trend. It still runs and still reports, with lowered confidence — refusing would be less useful than answering carefully.

---

## Implementer's checklist

For an agent running this end to end:

1. **Capture everything first, across every source, before analyzing anything.** If you're weighing importance while still reading a transcript, stop and finish capturing.
2. **One record per mention.** Three people saying the same thing is three records. The same person saying it three times is three records. Repetition is data; collapsing destroys both the count and the ability to quote.
3. **Record a reason for every exclusion.** Nothing leaves silently at any gate.
4. **Prefer `undetermined` to a guess.** Every field supports it. An honest gap is correctable; a confident error is not.
5. **Never infer churn risk from tone**, company size from speech, or that a capability is missing without checking whether it exists.
6. **Dedupe to accounts only at report time**, never at capture time — and size themes in accounts, so one loud customer can't dominate.
7. **Disclose captured-vs-counted** in the report, with the reason breakdown.
8. **Recompute every percentage inside the applied filter.**
9. **Quotes are real or absent.** A theme with no strong verbatim gets a stated quote gap, never a manufactured line.
10. **Check the three example reports before shipping** — `examples/` holds the reference standard for structure, tone and honesty.
