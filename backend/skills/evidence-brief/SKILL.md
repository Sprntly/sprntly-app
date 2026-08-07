---
name: evidence-brief
description: >
  The RENDERING CONTRACT for Sprntly's evidence brief — the markup, class
  vocabulary and component set every brief must be emitted in, so two briefs
  side by side read as the same template. This skill governs FORM ONLY: what
  the document is made of, not what it says. The analysis, the findings, the
  choice of what to show and the honesty rules are the calling prompt's job;
  this file never decides content. Bound by the evidence runner
  (app.evidence_kg / app.evidence_runner) on every evidence generation.
---

# Evidence Brief — the rendering contract

You are given the content decisions by the prompt around this method. **This
document decides only how that content is rendered.** Where the prompt and this
file appear to conflict about *what to say*, the prompt wins; this file is
authoritative for *markup*.

## The artifact

ONE self-contained HTML document, `1–3` pages, rendered in a **sandboxed iframe
with scripts disabled**. Nothing that needs JavaScript can ever run: no chart
library, no runtime, no inline handler, no `<script>` — such an element is not
merely discouraged, it renders as nothing at all.

## Hard rules

1. **Emit HTML and nothing else.** The first characters of your response are the
   document itself (`<meta …`). No preamble, no sign-off, no explanation, no
   markdown code fence. A single sentence before the first tag can make the
   whole brief fail to render.
2. **Document shape**, exactly:
   `<meta charset="utf-8">` → an **EMPTY** `<style></style>` → one
   `<div class="wrap">` holding the brief.
3. **Write no CSS.** Leave `<style>` empty. Sprntly splices the canonical
   stylesheet (`assets/evidence.css`) into that element server-side after
   generation, so every brief renders from one design system. CSS you emit is
   discarded — it only costs output tokens and drifts the look.
4. **Use only the canonical class vocabulary below.** A class the stylesheet
   does not define renders unstyled; an invented CSS variable renders as
   nothing.
5. **Charts are hand-authored inline `<svg viewBox="…">`**, drawn from the
   numbers you were given — never a chart library, an `<img>`, a screenshot or a
   placeholder. Keep the SVG responsive (the stylesheet already sets
   `svg{width:100%}`); wrap every chart in `<figure>` … `<figcaption>`.
6. **No external resources at all** — no web fonts, no stylesheets, no images,
   no network URLs. The document must render with no network.
7. **Never emit `class="hyp"`** or a "hypothesis / input to PRD" card. That
   component was retired and the viewer strips anything matching it.
8. **Omit, never stub.** A component with no content is left out entirely — no
   empty shell, no placeholder text.

## Canonical class vocabulary

Every class the injected stylesheet defines, and nothing else:

`.wrap` · `.eyebrow` · `.deck` · `.meta` · `.demo` · `.context` · `.tldr` ·
`.opp-top` + `.tag` · `.kicker` (+ `.o` opportunity-tone, `.n` neutral) ·
`.voc` · `.q` + `.ch` (+ `.rev` / `.sup` / `.sale`) · `.extract` ·
`.yes` / `.no` / `.us` (table cells) · `.ax` / `.vlabel` / `.blabel` (SVG text)

Plain elements are styled too and need no class: `h1`, `h2`, `p`, `section`,
`figure`, `figcaption`, `svg`, `table`, `th`, `td`.

Colour tokens available inside SVG (`fill`/`stroke="var(--…)"`): `--ink`,
`--paper`, `--muted`, `--hair`, `--problem`, `--problem-soft`, `--opp`,
`--opp-soft`, `--grid`, `--bar-neutral`.

## Component table — section → required markup

| Section | Markup |
|---|---|
| Eyebrow line above the title | `<p class="eyebrow">Evidence Brief · <source> → <team></p>` |
| Title + subtitle | `<h1>` then italic `<p class="deck">` |
| Author / date / status / pairs-with line | `<p class="meta">` (add `<p class="demo">` only for illustrative data) |
| TL;DR | `<div class="tldr"><h4>TL;DR</h4><p>…</p></div>` |
| Opportunity (one line) | `<div class="opp-top"><span class="tag">OPPORTUNITY</span><p>…</p></div>` |
| Context | `<p class="context"><b>Context.</b> …</p>` |
| Each finding | `<section>` → `<p class="kicker">` → `<h2>` → `<p>` → `<figure>` chart |
| Customer quotes | `<div class="voc">` of `<div class="q"><p class="ch rev\|sup\|sale">channel</p><p>quote</p></div>` |
| Competitive comparison | `<table>` with `.yes`/`.no`/`.us` cells, then `<div class="extract"><b>What I extract:</b> …</div>` |
| Convergence | `<section>` with an inline-SVG diagram: source nodes → one outcome box |

Document order is: eyebrow → title → deck → meta → TL;DR → opportunity →
context → findings → convergence. Components the prompt gives you no content
for are omitted, and the order of what remains does not change.

## Chart markup

Axis lines and gridlines: thin `stroke="var(--grid)"` / `stroke="var(--hair)"`.
Axis text `class="ax"`, value labels `class="vlabel"`, category labels
`class="blabel"`. Use `--problem` for the leak/problem series, `--opp` for the
opportunity/wedge series, `--bar-neutral` for a comparison baseline. Give the
`<svg>` a `role="img"` and an `aria-label`. Every `<figcaption>` is a
complete-sentence takeaway, not a label.

## Rendering checklist

- [ ] Response begins with `<meta` — no prose, no fence, before or after.
- [ ] `<style></style>` is empty; no CSS anywhere in the document.
- [ ] Exactly one `<div class="wrap">`, and every class used is in the
      vocabulary above.
- [ ] Every chart is inline `<svg>` in a `<figure>` with a `<figcaption>`.
- [ ] No `<script>`, no external URL, no `class="hyp"`.
- [ ] Empty components omitted rather than stubbed.

## Reference

`references/component-reference.html` is in your prompt: one document showing
every component and chart form in the canonical markup, with placeholder text.
Match its markup — its words are deliberately meaningless.
