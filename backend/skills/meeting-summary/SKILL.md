---
name: meeting-summary
description: Turn a meeting transcript or notes into a decision-first summary — decisions, owners, action items with due dates, and open questions — so nothing said in the room gets lost. Use when the user says "summarize this meeting", "meeting notes", "what did we decide", "action items from this call", or pastes a transcript. Leads with decisions and owners (not a play-by-play); flags unresolved threads; never invents a decision or an owner that wasn't stated.
---

# Meeting Summary

## What it does
Converts a transcript or rough notes into the summary people actually use: **decisions made**, **action items with owners and due dates**, and **open questions** — leading with what was decided, not a chronological replay. It captures commitments faithfully and never manufactures a decision, owner, or date that wasn't in the source.

## When to use / when NOT to use
- **Use** to summarize a meeting/call into decisions + actions.
- **Do NOT use** to synthesize research interviews (`interview-synthesis`) or write a status update (`status-report`).

## Inputs
- **Required:** the transcript or notes.
- **Optional:** attendee list, the meeting's purpose, prior action items. *If an owner or date wasn't stated, it's marked "owner: unassigned" / "no date" — never guessed.*

## Method (methodology)
1. **Extract decisions first** — what was actually agreed; quote the commitment where ambiguous.
2. **Action items** — task · owner (as stated, else unassigned) · due date (as stated, else flagged). 
3. **Open questions / unresolved** — threads that didn't close, with who owns the follow-up.
4. **One-line context** — purpose + attendees, kept short.
5. **No invention** — if it wasn't said, it isn't in the summary; ambiguity is surfaced, not resolved by guess.

## Output spec
Decisions (lead) · action items (owner + date) · open questions · brief context. Tables for actions; prose for decisions.

## Sprntly integration (optional)
- **Inputs:** prior action items from the knowledge graph (to mark done/carried).
- **Outputs:** decisions + actions registered; unresolved items raised as open questions/escalations.
- **Degrades to:** standalone from the transcript.

## Quality checklist (the bar)
- [ ] Leads with decisions, not a chronological replay.
- [ ] Every action has an owner + date, or is explicitly flagged unassigned/undated.
- [ ] Open/unresolved threads surfaced.
- [ ] Nothing invented — no decision/owner/date that wasn't stated.

## Known gaps / limitations
- Only as accurate as the transcript; garbled audio → flagged gaps, not filled.
- Can't infer intent behind vague statements — surfaces them as open questions.

## Worked example
**Input:** 40-min product sync transcript. Output: 3 decisions (lead), 6 action items (4 with owners+dates, 2 flagged unassigned), 2 open questions routed to owners. No invented commitments.
