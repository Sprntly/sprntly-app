---
name: campaign-ideas
description: Generate creative, channel-specific, mostly low-cost marketing ideas tied to a goal and audience — each with the channel, the message angle, and how you'd know it worked — so ideas are testable, not just a vibe list. Use when the user says "marketing ideas", "how do we get the word out", "growth ideas", "campaign ideas", "ways to acquire users". Anchors every idea to a goal + audience + a success signal; ranks by effort vs expected impact; flags the one cheap test to start with.
---

# Marketing Ideas

## What it does
Brainstorms marketing/acquisition ideas that are actually actionable: each idea names the **channel**, the **message angle**, the **audience**, and **how you'd measure if it worked** — then ranks them by effort vs. expected impact and points at the cheapest one to test first. Avoids the generic "do content marketing" list.

## When to use / when NOT to use
- **Use** to generate testable marketing/acquisition ideas for a specific goal.
- **Do NOT use** for full GTM strategy (`launch-gtm`), positioning (`positioning`), or growth-loop design (`growth-loop`).

## Inputs
- **Required:** the goal (awareness / sign-ups / activation) + the audience.
- **Optional:** budget, channels in play, brand voice, what's been tried. *No fabricated performance numbers — expected impact is a labeled estimate.*

## Method (methodology)
1. **Anchor** on the goal + audience + any constraints (budget, voice).
2. **Generate across channel types** — owned (content, community, product-led), earned (PR, partnerships, word-of-mouth), paid (targeted) — biased toward low-cost/high-leverage.
3. **Make each testable** — channel · message angle · audience · the success signal you'd watch.
4. **Rank effort × expected impact** (estimate labeled), flag the cheapest first test.
5. **Cut the generic** — anything that's just "be on social" gets sharpened or dropped.

## Output spec
Ideas (each: channel · angle · audience · success signal), an effort×impact rank, and the recommended first cheap test. Table for the ranked list.

## Sprntly integration (optional)
- **Inputs:** audience/ICP from `persona-segment`; positioning from `positioning`; goal from the knowledge graph.
- **Outputs:** the first test → `experiment-design`; ideas registered.
- **Degrades to:** standalone from goal + audience.

## Quality checklist (the bar)
- [ ] Every idea has a channel, angle, audience, and a success signal — testable, not vibes.
- [ ] Ranked by effort × expected impact (estimates labeled).
- [ ] A cheapest-first test named.
- [ ] Generic filler sharpened or cut.

## Known gaps / limitations
- Idea quality ≠ execution; channel fit must be tested, not assumed.
- Expected impact is an estimate — treat as a hypothesis to validate.

## Worked example
**Input:** goal "first 100 design-partner sign-ups," audience "AI-native PMs." Ideas: a teardown-style LinkedIn post mining real PM pain (owned, cheap, signal=DMs); a Claude-Code-community partnership (earned); a "show your loop" demo thread. Ranked; first test = the LinkedIn teardown.
