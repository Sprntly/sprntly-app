# Typography consistency spec — PRD · Evidence · Chat · Tickets

Status: **proposal / spec only** (no code changed). Goal: make the four core
surfaces read as one product ("similar yet different") by giving the three
loaded typefaces one job each and putting every surface on a single, shared
type scale.

Decisions this spec is built on:
- **Serif = hero titles only** (one big headline per surface, always weight 400).
- **Include the HTML-iframe surfaces** (v3 PRD / Evidence) — align them too.
- **Type + color pull focus to the right.** The right column (PRD / Evidence /
  Tickets) is the product's main work surface; the left **chat** column is
  secondary Q&A. The scale must make the right side read as primary and let the
  chat recede — consistency is the floor, hierarchy is the goal (see §2a).

---

## 1. Why the surfaces look unrelated today

Three families are loaded (`web/app/layout.tsx`): **Geist**, **Instrument
Serif**, **Geist Mono**. Four problems make them feel disjointed:

1. **`--font-display` and `--font-body` are the same font** (both Geist —
   `globals.css:97,99`). The "display vs body" distinction produces no visual
   contrast; all hierarchy is really size/weight/casing + serif + mono.
2. **Geist loads twice from two foundries** — Fontshare `geist@300..700` *and*
   Google `Geist` variable (`layout.tsx:52,56` + `@import` at `globals.css:1`).
   These are different typefaces sharing a family name; whichever wins the race
   renders, so weights can shift between loads.
3. **Instrument Serif is imported at `ital@0;1` only — no weight axis**
   (`layout.tsx:56`). It ships one weight (400), so every serif rule at 500/600
   renders **faux-bold** (synthetic). Affected today: `.tkv2-dtitle` (20/600),
   `.tkv2-topbar h2` (24/500).
4. **The v3 PRD/Evidence docs render inside sandboxed iframes** whose fonts come
   from model-generated HTML (`PrdHtmlView.tsx`, `EvidenceHtmlBrief.tsx`). They
   inherit **none** of the app's `--font-*` tokens — the single biggest source
   of "no synchrony."

### Concrete divergences (audit)

**Hero title — same role, no shared scale, two weights, two families:**

| Surface | Element | Family | Size / Weight |
|---|---|---|---|
| Chat — landing greeting | `.chat-greeting-title` | Serif | 40 (clamp 26–36) / 400 |
| Chat — brief-tab greeting | `.bc-greeting` | **Geist** | 15 / 400 ← should be serif |
| PRD — doc title | `.prd-title` | Serif | 28 / 400 |
| Evidence — doc title | `.prd-title` | Serif | 28 / 400 |
| Evidence — hero value | `.evv2-hero-value` | Serif | 22 / 400 |
| Tickets — board page title | inline (`TicketsScreen.tsx:601`) | Serif | 22 / 400 |
| Tickets — `tkv2` detail title | `.tkv2-dtitle` | Serif | 20 / **600 faux** |
| Tickets — inline detail title | inline (`TicketsScreen.tsx:395`) | **Geist** | 18 / 600 ← should be serif |

**Labels / eyebrows — same role, ~6 treatments:** Geist 10/700, Geist 10.5/600,
Geist 11/700, and **mono** 10–10.5, with letter-spacing 0.04–0.1em. Evidence and
PRD-v2 use mono for eyebrows; Chat and Tickets use Geist uppercase.

**Mono for the same concept, two ways:** a ticket ID is **mono** in
`TicketDetail` (`.tkv2-key`) but **Geist** in the board's inline detail
(`SPR-…`, `TicketsScreen.tsx:378`).

**Accidental serif leaks** from the global `h1–h6 { font-family: serif }` rule
(`globals.css:132`): assistant-markdown `h3` in Chat
(`.ai-bar-reply-answer h3`) and `.tkv2-sec h4` in Tickets inherit serif with no
one intending it.

**Two ticket-detail implementations disagree:** `TicketsScreen.tsx` (100% inline
styles) vs `TicketDetail.tsx` (`.tkv2` classes) style the same screen
differently.

---

## 2. Principles

1. **One job per family.**
   - **Instrument Serif** → the single hero title on each surface. This is the
     shared "voice" that unifies the product; the content beneath it differs per
     surface. Always weight **400** (never 500/600 — the weight isn't loaded).
   - **Geist** → everything structural: section headings, body, UI chrome, and
     **all** eyebrow/labels. One canonical label treatment.
   - **Geist Mono** → only genuine machine tokens: IDs, code, keyboard keys,
     metric deltas/moves, chart axes.
2. **Contrast comes from the scale, not from ad-hoc px.** Replace ~50 hand-tuned
   sizes with a small set of role tokens; every class references a token.
3. **Same role → same token, on every surface.** A ticket ID, a section eyebrow,
   a body paragraph must resolve to identical type wherever they appear.
4. **The iframe docs use the same tokens** via an injected base stylesheet + a
   constrained generation prompt.

---

## 2a. Focus hierarchy — pull the eye to the right

The app is a two-column workspace: **chat on the left, the work surface
(PRD / Evidence / Tickets) on the right.** Chat is where you *ask*; the right
side is what you're *building*. So the two columns should **not** carry equal
typographic weight — the type system is a focus tool, not just a consistency
tool.

**Primary (right — PRD / Evidence / Tickets):**
- Carries the **serif hero title** and the top of the size ladder
  (`--t-title` / `--t-title-sm`).
- Full-strength ink: titles and key values at **`--ink`**, body at `--ink`/`--ink-2`.
- Richer hierarchy is welcome — eyebrows, metric chips, mono IDs — because this
  is where attention should land.

**Secondary (left — Chat):**
- **No serif title in the working split.** Geist only; the eye shouldn't be
  pulled left by an editorial headline while a doc is open on the right.
- **One step quieter on both axes:** body at `--t-body-sm` (13) rather than 14,
  and **muted ink** — message body `--ink-2`, metadata `--ink-3`. This is the
  main lever: same family, lower contrast, so chat reads as a calm side panel.
- Keep interactive affordances (composer, action buttons) legible, but let
  passive thread text recede.

**The one exception — the empty landing state.** Before any work surface exists,
the chat *is* the whole screen, so the serif greeting (`--t-hero`) is the right
welcome moment. The rule is contextual: **serif greeting when chat is full-bleed
and empty; muted sans when chat is the left rail beside an open doc.** In
practice that means the greeting's serif treatment lives on the empty/landing
state only, and the threaded split-view chat uses the secondary tokens above.

Color, therefore, is a first-class part of this spec alongside size/weight:
`--ink` (primary titles/values, right side) → `--ink-2` (body) → `--ink-3`
(chat body / metadata) → `--ink-4` (timestamps, faint). Use ink **strength** to
signal primary-vs-secondary, not just size.

---

## 3. Font loading fixes (`layout.tsx` / `globals.css`)

- **Load Geist from one source.** Drop either the Fontshare link
  (`layout.tsx:52`) or the Google `@import` (`globals.css:1`) — keep one so
  weights render identically. (Recommend keeping the Google variable font and
  removing the Fontshare link, or vice-versa — pick one and delete the other.)
- **Instrument Serif:** since serif is now 400-only, the current
  `Instrument+Serif:ital@0;1` import is correct — just make sure **no rule sets
  serif weight ≠ 400**. (Alternatively, if a bold serif is ever wanted, add the
  weight to the import; not needed for this spec.)
- Keep Geist Mono at `wght@400;500`.

---

## 4. The shared type scale (proposed tokens)

Add to `:root` in `globals.css`. Families first (note the honest rename):

```css
/* Families — one job each */
--font-serif: 'Instrument Serif', Georgia, serif;   /* hero titles only, 400 */
--font-sans:  'Geist', -apple-system, sans-serif;   /* everything structural  */
--font-mono:  'Geist Mono', 'JetBrains Mono', monospace; /* machine tokens     */
/* keep --font-body / --font-display as aliases of --font-sans during migration */
```

Role tokens (size / line-height / weight / tracking). These are the *only*
values any surface should use:

| Token | Family | Size | LH | Weight | Tracking | Role |
|---|---|---|---|---|---|---|
| `--t-hero` | serif | clamp(28px, 4vw, 40px) | 1.1 | 400 | −0.01em | Chat landing greeting |
| `--t-title` | serif | 28px | 1.15 | 400 | −0.01em | Full-page doc title (PRD, Evidence) |
| `--t-title-sm` | serif | 22px | 1.2 | 400 | −0.01em | Panel/detail title (Ticket detail, board page title) |
| `--t-h2` | sans | 15px | 1.35 | 600 | −0.005em | Inline subheads (markdown h3, etc.) |
| `--t-eyebrow` | sans | 10.5px | 1.3 | 600 | 0.08em | Section eyebrow (UPPERCASE) — replaces `.prd-h2` pattern |
| `--t-body` | sans | 14px | 1.6 | 400 | −0.003em | Primary body / messages |
| `--t-body-sm` | sans | 13px | 1.55 | 400 | −0.003em | Dense body (cards, panels) |
| `--t-label` | sans | 11px | 1.3 | 600 | 0.06em | THE canonical eyebrow/label (UPPERCASE) |
| `--t-meta` | sans | 12px | 1.4 | 400 | 0 | Counts, timestamps, secondary meta |
| `--t-mono` | mono | 12px | 1.4 | 500 | 0 | IDs, code, deltas, axes, kbd |

Serif ladder is now intentional: **40 → 28 → 22**, all weight 400. Labels
collapse to **one** treatment (`--t-label`). Mono is one size.

> Naming (`--t-*`) is a suggestion; match whatever token convention the team
> prefers. The point is the *set*, not the prefix.

---

## 5. Per-surface mapping (from → to)

Only the rows that change are listed; everything already matching a token stays.
**PRD / Evidence / Tickets are the primary (right) column** — their titles and
key values sit at full `--ink` and the top of the ladder; Chat is deliberately
one step quieter (§2a).

### Chat (`ChatScreen.tsx`, `BriefChat.tsx`, `.bc-*`, `.ai-bar-reply-*`)
Chat is the **secondary** column — it recedes in the working split (see §2a).
| Element | Now | → Token + emphasis |
|---|---|---|
| Landing greeting `.chat-greeting-title` (empty/full-bleed only) | serif 40/400 | `--t-hero`, `--ink` — the welcome moment, serif allowed here |
| Brief-tab greeting `.bc-greeting` (empty-state welcome) | Geist 15 | match the landing greeting's treatment for the same empty state; **stays sans/quiet once the brief has content** |
| Assistant markdown `h2` `.ai-bar-reply-answer h2` | Geist 18/600 | `--t-h2` |
| **Assistant markdown `h3`** | **serif (leak)** | **`--t-h2`** (explicit sans) |
| Thread body `.bc-user-bubble`, `.bc-agent-body` | Geist 14, `--ink`/`--ink-2` | **`--t-body-sm` (13), `--ink-2`** — one step quieter so the right side leads |
| Agent status / timestamps / badges | Geist, mixed | `--t-meta`, **`--ink-3`/`--ink-4`** |
| Composer, action buttons | Geist | keep legible (`--t-body`, `--ink`) — affordances don't recede |
| Slash trigger, `⌘/` kbd, inline code | mono | `--t-mono` |

> Net effect: in the split view, chat is Geist + muted ink + 13px body, while
> the right-hand doc carries the serif title, full `--ink`, and 14px body —
> so the eye lands right by design.

### PRD (`PrdPanelContent.tsx`, `.prd-*`, `.prdv2-*`)
| Element | Now | → Token |
|---|---|---|
| Doc title `.prd-title` | serif 28/400 | `--t-title` |
| Section eyebrow `.prd-h2` | Geist 10.5/600 up | `--t-eyebrow` (unify tracking to 0.08) |
| Body `.prd-body p/li` | Geist 14 / 14.5, lh 1.7 | `--t-body` (lh 1.6) |
| **All `.prdv2-*` mono eyebrow labels** (`-req-cat`, `-ac-id`, `-qa-*`, `-sev`…) | **mono 10–10.5** | **`--t-label`** (sans) — keep mono only for the metric-move chips + IDs |
| Metric moves `.prdv2-metric-move` etc. | mono | `--t-mono` |

### Evidence (`DetailScreen.tsx`, `.evv2-*`, shared `.prd-*`)
| Element | Now | → Token |
|---|---|---|
| Doc title `.prd-title` | serif 28/400 | `--t-title` |
| Hero value `.evv2-hero-value` | serif 22/400 | keep serif, but this is a **metric**, not a title — move to `--t-title-sm` *or* reclassify as a big mono/sans number (see note) |
| Section eyebrow `.prd-h2`, `.evv2-*-h` | mixed Geist 10/10.5, 700 | `--t-eyebrow` |
| Cuts-index headline, quote channel | mono | `--t-label` (channel) / keep `--t-mono` (index) |
| Finding tags (`tag-domain`/`tag-sub`) | **no rule → default Geist** | give them `--t-label` (currently a styling gap) |

> Evidence hero value uses serif to look like a headline, but it's a number.
> Decide: either keep it in the serif ladder (`--t-title-sm`) for editorial
> feel, or make big metrics a **sans/mono** role so serif stays title-only.
> Recommend the latter for a stricter "serif = titles" rule.

### Tickets (`TicketsScreen.tsx` inline, `TicketDetail.tsx` `.tkv2-*`)
| Element | Now | → Token |
|---|---|---|
| Board page title (`:601`) | serif 22/400 | `--t-title-sm` |
| **Inline detail title (`:395`)** | **Geist 18/600** | **`--t-title-sm`** (make it serif — matches `tkv2` detail) |
| `tkv2` detail title `.tkv2-dtitle` | serif 20/**600 faux** | `--t-title-sm` (400) |
| **`.tkv2-sec h4`** | **serif (leak)** | `--t-eyebrow` (explicit sans) |
| Ticket ID `.tkv2-key` | mono | `--t-mono` |
| **Inline board ID `SPR-…` (`:378`)** | **Geist 13/500** | **`--t-mono`** (unify with `.tkv2-key`) |
| Field/section labels | Geist 11–12, 500–700, up | `--t-label` |

> Longer-term: the two ticket-detail implementations should converge on one
> (prefer the `.tkv2` class-based `TicketDetail.tsx`); this spec assumes both are
> brought onto the tokens in the interim.

---

## 6. Aligning the HTML-iframe docs (v3 PRD & Evidence)

These render model-generated HTML via `srcDoc` and ignore the app CSS. Two
coordinated changes:

1. **Inject a shared base `<style>` into the iframe `srcDoc`** in
   `PrdHtmlView.tsx` and `EvidenceHtmlBrief.tsx` — before the model's markup —
   that (a) `@import`s the same Geist / Instrument Serif / Geist Mono, and
   (b) defines the same `--t-*` tokens and base element rules (`body` →
   `--t-body`, `h1` → `--t-title`, `h2` → `--t-eyebrow`, `code` → `--t-mono`).
   A model doc that sets *no* fonts then inherits the system automatically.
2. **Constrain the generator prompts** (`ds-agent` / `prd-author` and the
   evidence skill) to emit semantic HTML and use the token variables /
   utility classes rather than inventing font-families. Provide the token list
   in the skill prompt so generated CSS references `var(--t-*)`.

Result: generated PRDs/Evidence match the app even though they live in an
iframe. This is required for true cross-surface synchrony — fixing globals.css
alone leaves these two surfaces off-system.

---

## 7. Suggested rollout (when we implement)

1. Add family + `--t-*` tokens to `globals.css`; keep `--font-display/-body`
   aliases so nothing breaks mid-migration.
2. Fix loading (single Geist source) and remove all faux-bold serif.
3. Add explicit `font-family` to the two serif leaks (`ai-bar-reply-answer h3`,
   `tkv2-sec h4`).
4. Refactor `.prd-*`, `.prdv2-*`, `.evv2-*`, `.tkv2-*`, `.bc-*` to reference
   tokens (surface by surface; visually diff each).
5. Port `TicketsScreen.tsx` inline styles onto the tokens (or migrate to
   `TicketDetail.tsx`).
6. Inject the iframe base stylesheet + update generator prompts.
7. Remove the `--font-display/-body` aliases once no rule references them.

No behavior changes here — this document is the review artifact; implementation
is a follow-up.
