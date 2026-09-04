"use client"

/**
 * The finished Goal Analysis, rendered as a REPORT rather than a list.
 * (Engine name Crucible; that word never appears on screen.)
 *
 * WHY A DOCUMENT. The same facts in a list of chips are read as a dashboard —
 * scanned, ranked by the biggest number, and trusted in proportion to how
 * finished they look. Read as prose they are read as an argument: this is the
 * goal, this is what was read to answer it, this is what it says, and this is
 * what it still cannot tell you. The last part is the product's actual claim
 * and it is the first thing a dashboard drops.
 *
 * WHAT THIS ENGINE CAN HONESTLY SAY TODAY. It is qualitative. It has themes
 * from the knowledge graph, the source documents behind each one, reach (how
 * many accounts a theme touches — frequently unknown), confidence bands,
 * adjudication, a considered-and-dropped ledger with reasons, coverage notes,
 * and the run plan. It has NO point estimates, no effort, no RICE, no
 * significance tests, because the graph holds prose and none of those can be
 * computed from prose. Every place a number is missing, this report says so
 * and says why, in the section it would have appeared in. A silently omitted
 * section reads as "not applicable"; a stated absence reads as "not known",
 * and only one of those is true.
 *
 * THE RULES THIS FILE EXISTS TO KEEP:
 *  - An unsized finding renders as "could not be sized", NEVER as 0. They lead
 *    to opposite decisions (I3).
 *  - Every finding that has source documents shows them, beside the claim they
 *    support, so a reader can check it rather than trust it.
 *  - Coverage notes sit ABOVE the findings they qualify. A run that read a
 *    third of the evidence must not be indistinguishable from a complete one.
 *    (This is why they render inside "What was read" and not in a footer, even
 *    though a footer is where a report would conventionally put them.)
 *  - The closing section is built from the run plan's own gaps, so what the
 *    user was warned about BEFORE the run is what they are reminded of after.
 *
 * THIS COMPONENT IS STILL PURE. `editable` adds two BUTTONS and nothing else —
 * the fetching, the document and the editor all live in `GoalAnalysisTab` and
 * `GoalReportDocument`. That matters because this file owns the rules above,
 * and a component that also owned an async lifecycle would be one nobody could
 * test those rules against without a server.
 */
import { EFFORT_ABSENT, MAX_RICE_ROWS, RICE_INPUT_COUNT, riceFor } from "../../lib/goalRice"
import {
  CALL_COUNT_FLOOR_NOTE, MAX_MOSCOW_ROWS, TYPE_BUCKET_BLOCKER,
  TYPE_BUCKET_PREFERENCE, hasCallCount, moscowFor, typeBucket,
} from "../../lib/goalMoscow"
import {
  DATA_GAPS_HEADING, ONE_TOPIC_NOTE, dataGapsFor, optionHeader,
  optionNumbers, optionsAreOneTopic,
} from "../../lib/goalDataGaps"
import { stop, stripClaimRefs, upperFirst } from "../../lib/goalProse"
import { frameworkDisplayName } from "../../lib/goalFrameworkDisplay"
import type { ReactNode } from "react"
import type { GoalFinding, GoalRunDetail, GoalRunPlan } from "../../lib/api"
import styles from "./GoalAnalysisReport.module.css"

/** How many rejections render expanded. Beyond this the ledger folds, because
 *  a run can drop a hundred candidates and an unfolded hundred buries the
 *  closing section under them. */
const RULED_OUT_OPEN_MAX = 12

/** THE CAVEAT THAT TRAVELS WITH EVERY KILL SIGNAL. A kill signal here comes
 *  out of a corpus of what people said — there is no metric series behind it
 *  and nothing watches it — so the line is a belief a reader can go and
 *  disprove, never a measured trigger. It renders INLINE, in the same
 *  paragraph as the signal, so it cannot be skimmed past the way a footnote
 *  can. The exported document (`backend/app/crucible/report.py`,
 *  `KILL_SIGNAL_CAVEAT`) carries the same sentence; the two renderers share
 *  no code, so they are kept in step by hand. */
const KILL_SIGNAL_CAVEAT =
  "This is a falsifiable belief, not a measured threshold — this analysis "
  + "reads what people said, not a metric series, so nothing is watching for "
  + "this on your behalf. Someone has to go and look."

/** How many findings get a FULL write-up, and how many of the rest get one
 *  line — mirroring `backend/app/crucible/report.py`'s
 *  `MAX_WRITTEN_UP_FINDINGS` / `MAX_OTHER_CONSIDERED_ROWS`.
 *
 *  BOTH COME FROM THE READER, NOT FROM A LIMIT. Asked what he wanted this to
 *  be, he described it himself: "finding number one could be, hey, maybe
 *  build XYZ … and then we go to item number two … and then the bottom will
 *  be other things that we considered — these are 20 other things that you
 *  could also build." Two write-ups, then twenty lines. Deliberately not
 *  derived from `MAX_RICE_ROWS` or any size budget, so a later change to one
 *  of those cannot silently move an editorial decision.
 *
 *  NOTHING IS DROPPED: everything past the twenty is counted in a sentence,
 *  and everything past that is still on the run. */
const MAX_WRITTEN_UP_FINDINGS = 2
const MAX_OTHER_CONSIDERED_ROWS = 20

/** The set-aside appendix, capped the same way and for the same reason: at 95
 *  rows a table of what was NOT the answer was named as one of the things
 *  making this unreadable. The heading still carries the true total. */
const MAX_SET_ASIDE_ROWS = MAX_OTHER_CONSIDERED_ROWS

/** A chart, drawn in characters.
 *
 *  The exported document (`report.py`) has no choice: it is stored in
 *  `custom_artifacts.body_html`, whose sanitizer drops `<svg>` with its
 *  children and keeps no `width`/`height`/`border` CSS, so a bar there has to
 *  be made of glyphs. This panel is not sanitized and could draw a real box —
 *  but the two renderers share no code and drift by exactly this kind of
 *  "here we can do better", so the panel draws the same bar from the same
 *  proportion. It also means a reader comparing the panel against the
 *  exported document sees one chart, not two that disagree.
 *
 *  Colour and numeral face are the canonical report palette
 *  (`backend/skills/prd-author/assets/prd.css`), carried by the stylesheet
 *  beside this component. */
const BAR_CELLS = 20

/** A bar proportional to `largest`, or null when there is nothing honest to
 *  draw. NEVER DRAWN FOR AN UNSIZED VALUE (I3): null renders as "Not
 *  measured" at the call site, because a zero-length bar in a column of long
 *  ones asserts "small", which is the one thing an unknown must never say.
 *  A non-zero value always gets at least one cell, so real-but-tiny stays
 *  visible. Mirrors `report.py`'s `_bar`. */
function Bar({ value, largest }: { value: number | null; largest: number }) {
  if (value == null || !(value > 0) || !(largest > 0)) return null
  const cells = Math.max(1, Math.min(BAR_CELLS, Math.round(BAR_CELLS * value / largest)))
  return <span className={styles.bar}>{"\u2588".repeat(cells)}</span>
}

/** The largest reach among a set of rows — the bar's scale. An unsized
 *  finding contributes nothing to it and draws nothing. */
function largestReach(rows: readonly GoalFinding[]): number {
  return rows.reduce((n, f) => Math.max(n, f.impact_value ?? 0), 0)
}

/** A clipped finding label/statement for one line of the compact overflow
 *  list — mirrors `report.py`'s `_esc_statement` / `MAX_STATEMENT_CHARS`
 *  (400), cut on a word boundary. React escapes text content itself, so
 *  unlike the backend's HTML string this needs no separate escaping step. */
const MAX_OVERFLOW_STATEMENT_CHARS = 400
function overflowStatement(f: GoalFinding): string {
  const text = ((f.label || "").trim() || f.statement).replace(/\s+/g, " ").trim()
  if (text.length <= MAX_OVERFLOW_STATEMENT_CHARS) return text
  const cut = text.slice(0, MAX_OVERFLOW_STATEMENT_CHARS)
  const lastSpace = cut.lastIndexOf(" ")
  return (lastSpace > 0 ? cut.slice(0, lastSpace) : cut) + "…"
}

/** An excluded source is only a KEY by the time the report runs — its label
 *  went with the entry the run dropped. Rather than keep a second copy of the
 *  backend's source prose here, where it would drift, the key is softened into
 *  something readable: `project_mgmt` reads as "project mgmt", not as a column
 *  name the reader has to decode. */
function humanSource(sourceType: string): string {
  return sourceType.replace(/_/g, " ")
}

/** Reach, in words. NULL is "could not be sized" and is never rendered as a
 *  number — a 0 and an unknown look alike and mean opposites (I3). */
function reach(f: GoalFinding): string {
  if (f.impact_value == null) return "Could not be sized"
  if (f.currency === "accounts") {
    return `${f.impact_value} account${f.impact_value === 1 ? "" : "s"}`
  }
  return `${f.impact_value}${f.currency ? ` ${f.currency}` : ""}`
}

/** The size of a finding, wherever it is shown. `idPrefix` exists because the
 *  headline repeats the leading finding's size, and two elements carrying the
 *  same test id would make "renders unsized, never zero" ambiguous — the one
 *  assertion in this file that must never be ambiguous. */
function Sized({ f, idPrefix = "goal" }: { f: GoalFinding; idPrefix?: string }) {
  const unsized = f.impact_value == null
  return (
    <span
      className={`ga-size${unsized ? " ga-size--unknown" : ""}`}
      data-testid={unsized ? `${idPrefix}-unsized` : `${idPrefix}-sized`}
    >
      {reach(f)}
    </span>
  )
}

//: A stable empty array, so every card that is not the recommended one gets
//: the same `dataGaps` identity instead of a fresh `[]` literal on each render.
const EMPTY_GAPS: readonly string[] = []

/** One ranked finding, written out as a memo item: the action, then — in two
 *  columns beneath it — the argument on the left and the evidence it rests on
 *  on the right. */
function ReportFinding({
  f, rank, sharedWeakest = false, sharedCap = false,
  sharedAssumptions = false, option = 0, dataGaps = [],
  oneTopic = false, oneTopicNote = "", optionTotal = 0,
  deferComparison = false, deferGaps = false, showCallNote = true,
}: {
  f: GoalFinding
  rank: number
  /** `Option N` among the deep write-ups, or 0 for a finding that is not one.
   *  A LABEL AND NOTHING ELSE — `optionNumbers` numbers the run's own frozen
   *  rank order (I10); nothing here groups, scores or chooses (I2). */
  option?: number
  /** What is still unknown about the finding being RECOMMENDED — empty on
   *  every other card. Assembled deterministically by `dataGapsFor` from
   *  fields the engine already produced; no model call. */
  dataGaps?: readonly string[]
  /** Set on EVERY card of a run whose top two write-ups name the same topic.
   *  Suppresses the option numbering; the note explaining why is rendered
   *  once, in "Why number one". */
  oneTopic?: boolean
  /** How many deep write-ups this run rendered — decides whether a single one
   *  is headed as "the" recommendation or as "Option 1" of several. */
  optionTotal?: number
  /** Rendered once, on the recommended card, when the comparison is NOT being
   *  deferred to its own section below. */
  oneTopicNote?: string
  /** "Why this over the next" reads AFTER both write-ups when there are two
   *  of them — "but I think you should do number one because it's the most
   *  important one" is a comparison between them, not a footnote inside the
   *  first. With only one write-up there is no "next" to read it after, so it
   *  stays on the card. Mirrors `report.py`'s `defer_comparison`. */
  deferComparison?: boolean
  /** `dataGaps` still arrives when the gaps are lifted out to "Before you
   *  spend": the card has to KNOW it carries them, because that is also what
   *  suppresses its own open-questions list — the middle of the same list. */
  deferGaps?: boolean
  /** The call-count floor note is ONE fact about how the corpus was ingested,
   *  not a judgement about this finding, and it used to print under every card
   *  that showed a call count. The section sets this for the first card it
   *  applies to and clears it for the rest. */
  showCallNote?: boolean
  /** Hoisted to the top of the section because every finding assumes the
   *  identical thing. Suppressed here rather than emptied upstream, so the
   *  finding itself is untouched and the two renderers cannot disagree about
   *  what a finding assumed. */
  sharedAssumptions?: boolean
  /** The section already stated this sentence once, because every finding
   *  carries the identical one. See `sharedReason` below. */
  sharedWeakest?: boolean
  sharedCap?: boolean
}) {
  const deep = f.deep_recommendation
  const hasDeep = Boolean((deep?.action || "").trim() && (deep?.because || "").trim())
  const flat = f.recommendation
  const hasFlat = Boolean((flat?.action || "").trim() && (flat?.because || "").trim())

  // ── WHAT TO DO, THEN WHY, THEN WHAT IT RESTS ON. ──────────────────────
  //
  // The action leads the card — "this is only the issues, no suggestion on
  // how to solve or what's the exact recommendation from it" — and the
  // argument for it runs directly underneath at its own measure. What the
  // argument rests on is a strip below, not a column beside: see `.card` in
  // the stylesheet for why the two-column version this replaced gave the
  // prose a 45-character measure and the evidence an empty half.
  //
  // ABSENT IS NORMAL, not an error: only the top findings get a suggestion,
  // and anything that quoted a figure, promised an outcome or failed the
  // lint was dropped rather than repaired. THE DEEP PASS TAKES PRECEDENCE
  // over the flat one when both exist — the same findings feed both LLM
  // calls, and showing both would put two suggestions on one finding.
  //
  // NO ACCOUNT IS NAMED, AND THAT IS NOT AN OMISSION: a stored finding
  // carries how MANY accounts a theme touches and never which. The strip
  // states the reach and names the SOURCE DOCUMENTS, which is what is
  // actually on the record.
  const why = hasDeep ? (
    <div className={styles.prose}>
      <p className="ga-finding-rec-why">
        <em>Why.</em> {stripClaimRefs(deep!.because)}
      </p>
      {deep!.changes.length ? (
        <>
          <p className={styles.blockLabel}>What to change</p>
          <ul className={styles.evList} data-testid="goal-finding-changes">
            {deep!.changes.map((c, i) => (
              <li key={i}>
                {stripClaimRefs(c.text)}
                {/* PROVENANCE RECEDES: its own line, smaller and lighter, so
                    it supports the change rather than competing with it. */}
                <span className={styles.cite}>from: “{c.cited_claim}”</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {/* SUPPRESSED ON THE CARD THAT CARRIES THE GAPS LIST — these same
          questions are the middle of it. */}
      {deep!.open_questions.length && !dataGaps.length ? (
        <>
          <p className={styles.blockLabel}>Still open</p>
          <ul className={styles.evList}>
            {deep!.open_questions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </>
      ) : null}
      {deep!.what_would_falsify ? (
        <p className="ga-weakest" data-testid="goal-finding-kill-signal">
          <b>Kill signal.</b> {stripClaimRefs(deep!.what_would_falsify)}{" "}
          <em>{KILL_SIGNAL_CAVEAT}</em>
        </p>
      ) : null}
      {deep!.comparison && !deferComparison ? (
        <p className="ga-weakest" data-testid="goal-finding-comparison">
          <b>Why this over the next.</b> {deep!.comparison}
        </p>
      ) : null}
      {oneTopicNote && !deferComparison ? (
        <p className="ga-weakest" data-testid="goal-finding-one-topic">
          <b>Why these are not two options.</b> {oneTopicNote}
        </p>
      ) : null}
    </div>
  ) : hasFlat ? (
    <div className={styles.prose}>
      <p className="ga-finding-rec-why">
        <em>Why.</em> {stripClaimRefs(flat!.because)}
      </p>
      {/* THE SHORTFALL, CONNECTED TO THE FINDING IT ACTUALLY DROPPED.
          `deep_attempted` is only set on a finding that was IN the top N but
          whose evidence did not clear the citation gate — never on one
          simply ranked past N. The specific reason lives once, under how
          this was produced, and this points there. */}
      {f.deep_attempted ? (
        <p className="ga-weakest" data-testid="goal-finding-deep-shortfall">
          This was in line for a full write-up and did not get one this run —
          see “How many got a full recommendation” under how this was
          produced. The suggestion above is the plain version, not a downgrade
          of a deeper one you are missing.
        </p>
      ) : null}
    </div>
  ) : null

  // Label/value rows: the facts that are pairs rather than prose. A row with
  // nothing in it is not rendered, so the strip is as short as the evidence.
  const rows: [string, ReactNode][] = []
  rows.push([
    "Scale",
    <>
      <Sized f={f} />
      {f.confidence_band ? <> · {f.confidence_band} confidence</> : null}
      {f.adjudication === "conflict" ? (
        <>
          {" "}·{" "}
          <span
            className="ga-conflict"
            title="Two sources that may both speak to this disagree"
          >
            sources disagree
          </span>
        </>
      ) : null}
      {f.claim_ids?.length ? (
        <> · {f.claim_ids.length} claim{f.claim_ids.length === 1 ? "" : "s"}</>
      ) : null}
    </>,
  ])
  // NO GROUNDED-MONEY ROW HERE, AND IT IS NOT AN OVERSIGHT. The exported
  // document renders "customers named $X across N accounts" from
  // `impact.native_units`; `GoalFinding` does not carry `impact`, so the API
  // never sends it to this panel. Reconstructing the figure from anything
  // else would be inventing it, and a money line is the last place to guess.
  // Closing the gap means widening the payload, not the renderer.
  if (f.surfaced_by?.length) {
    rows.push(["Sources", <span data-testid="goal-sources">{f.surfaced_by.join(" · ")}</span>])
  }
  // The weakest leg is the actionable half of a confidence score: it says
  // what to go and find out, which a band on its own never does. Suppressed
  // when it is the same sentence on every row — one fact about the corpus
  // printed 32 times reads as 32 separate judgements.
  if (f.confidence?.weakest_leg_reason && !sharedWeakest) {
    rows.push(["Weakest link", f.confidence.weakest_leg_reason])
  }
  if (f.confidence?.cap_reason && !sharedCap) {
    rows.push(["Capped", f.confidence.cap_reason])
  }
  // I8: every assumed parameter is disclosed where the number is read, not
  // in a methodology page nobody opens.
  if (f.assumed_params?.length && !sharedAssumptions) {
    rows.push([
      "Assumes",
      <ul>
        {f.assumed_params.map((p) => (
          <li key={p.name}>
            <b>{p.name}</b>: {p.basis}
          </li>
        ))}
      </ul>,
    ])
  }
  // THE FLOOR, SAID IN WORDS, AND SAID ONCE. A call provider is extracted one
  // pass per call, so a collapsed entry carries how many CALLS it stands for
  // — but anything ingested before that changed was batched several calls to
  // a document, so the number can only be a lower bound. One fact about
  // ingest, not about this finding: `showCallNote` is set by the section for
  // the first card it applies to.
  if (showCallNote && hasCallCount(f.surfaced_by ?? [])) {
    rows.push([
      "Note",
      <span data-testid="goal-call-count-floor">{CALL_COUNT_FLOOR_NOTE}</span>,
    ])
  }

  return (
    <li className={`ga-doc-finding ${styles.card}`} data-testid="goal-finding">
      {/* THE THEME IS THE HEADING. It used to be the whole sentence — "30
          claims across 11 accounts concern “Sales Pipeline” — for example, …"
          — so the one word a reader scans for sat mid-clause, in quotes,
          behind two numbers the chips on the next line repeat verbatim.
          FALLS BACK TO THE SENTENCE for runs stored before `label` shipped. */}
      <div className="ga-doc-finding-head">
        <span className="ga-doc-rank" aria-hidden="true">{rank}</span>
        <p className="ga-finding-statement">
          {(f.label || "").trim() || f.statement}
        </p>
      </div>
      {hasDeep ? (
        <p className={styles.action} data-testid="goal-finding-recommendation">
          {/* EXACTLY ONE CARD MAY BE HEADED AS THE RECOMMENDATION, and which
              header each card gets is `optionHeader`'s decision, so the panel
              and the exported document cannot word it differently. */}
          <strong>{optionHeader(option, optionTotal, oneTopic)}</strong>{" "}
          {stripClaimRefs(deep!.action)}
        </p>
      ) : hasFlat ? (
        // NOT "Recommended.", WHICH IS THE DEEP CARD'S WORD. This is the
        // one-line pass over a finding that did NOT get a full write-up.
        <p className={styles.action} data-testid="goal-finding-recommendation">
          <strong>Suggested.</strong> {stripClaimRefs(flat!.action)}
        </p>
      ) : null}
      {why}
      <div className={styles.rests}>
        <p className={styles.blockLabel} style={{ marginTop: 0 }}>
          What this rests on
        </p>
        {rows.map(([key, value], i) => (
          <div className={styles.restsRow} key={i}>
            <span className={styles.restsKey}>{key}</span>
            <span className={styles.restsVal}>{value}</span>
          </div>
        ))}
        {/* ONE CLAIM, IN ITS SOURCE'S OWN WORDS — "this is exactly what they
            said". Full width under the rows rather than squeezed into a value
            column: it is the only thing in the card being quoted, and the one
            place the document changes voice. Only when the heading is the
            label — with the sentence as the heading the quote is already
            inside it. */}
        {(f.label || "").trim() && (f.example || "").trim() ? (
          <blockquote className={styles.quote} data-testid="goal-finding-example">
            “{f.example}”
          </blockquote>
        ) : null}
      </div>
      {/* ── WHAT WE DO NOT KNOW ABOUT WHAT WE JUST RECOMMENDED. ────────────
          Only on the recommended card, assembled deterministically from
          fields the engine already produced — no model call, nothing scored
          (I2). GAPS, NOT ACTIONS. Lifted out to "Before you spend" when
          `deferGaps` is set; the card still has to KNOW it carries them,
          because that is what suppresses its own open-questions list. */}
      {dataGaps.length && !deferGaps ? (
        <div data-testid="goal-finding-data-gaps">
          <p className={styles.blockLabel}>{DATA_GAPS_HEADING}</p>
          <ul className={styles.evList}>
            {dataGaps.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </li>
  )
}

export function GoalAnalysisReport({
  run,
  editable = false,
  onEdit,
  onSaveCopy,
  busy = false,
}: {
  run: GoalRunDetail
  /** Show the document actions. DEFAULT FALSE, so every existing caller — and
   *  every existing test — renders exactly what it rendered before. */
  editable?: boolean
  /** Turn this report into an editable document and open it. The endpoint
   *  behind it is idempotent, so a double press cannot fork a second copy. */
  onEdit?: () => void
  /** Save a SEPARATE copy as an ordinary team document, leaving this report
   *  alone. The fork half of "edit in place AND fork on demand". */
  onSaveCopy?: () => void
  /** A document action is in flight. Both buttons disable together: they write
   *  to the same run, and letting the second fire while the first is still
   *  going is how you get a copy of a report that is mid-creation. */
  busy?: boolean
}) {
  const plan: GoalRunPlan | undefined = run.prioritisation?.plan
  // How many findings got a full recommendation, and why — a sentence
  // computed from the goal's own ask, never a bare number. Mirrors
  // `report.py`'s `_recommendation_basis_section`, same placement.
  const recommendationBasis = (run.prioritisation?.recommendation_basis || "").trim()
  // Corpus-wide list pricing, in the exported document's own words — see
  // `report.py`'s `_list_pricing`/`_findings_section`. UNCONDITIONAL, unlike
  // `recommendationBasis` above: that one only answers a money target the
  // reader named, this renders whenever any finding carries pricing units.
  const listPricingBasis = (run.prioritisation?.list_pricing_basis || "").trim()
  // The single, top-line recommendation for the whole report — narrated
  // across the per-finding deep recommendations already shown above, never a
  // replacement for them. Mirrors `report.py`'s
  // `_synthesized_recommendation_section`, including the same "silent when
  // empty" rule: `action`/`because` are blank exactly when there was
  // nothing to synthesize (see `GoalRunDetail["prioritisation"]
  // ["synthesized_recommendation"]`'s own comment in `api.ts`).
  const synthesizedRecommendation = run.prioritisation?.synthesized_recommendation
  const synthesizedAction = (synthesizedRecommendation?.action || "").trim()
  const synthesizedBecause = (synthesizedRecommendation?.because || "").trim()
  const synthesizedCitations = (synthesizedRecommendation?.citations || []).filter(
    (c) => (c.evidence || "").trim(),
  )
  // Whether `judge_relevance` actually ran on this run — turns the
  // "these findings were not selected"/"were filtered" branch below. Never
  // guessed from `setAside.length`: a gate that judged everything `true`
  // still ran. Mirrors `report.py`'s `relevance_gate_ran`.
  const relevanceGateRan = Boolean(run.prioritisation?.relevance_gate_ran)
  // The relevance gate's coverage disclosure — see the funnel section below.
  const relevanceJudged = run.prioritisation?.relevance_judged
  // ── THE THEME, THE QUOTE AND THE RECOMMENDATION, MERGED IN ONCE. ────────
  //
  // These three live in the run's own JSON rather than in columns on
  // `crucible_findings` — adding columns means a migration against the shared
  // Supabase, which is a production change. Mirrors `render_report_html`
  // exactly, including the length check: the merge is POSITIONAL because the
  // findings arrive in rank order, and attaching one finding's recommendation
  // to another is far worse than showing none.
  // ── THE GOAL-RELEVANCE GATE. ──────────────────────────────────────────
  //
  // `set_aside_by_rank[i]` is the reason finding `i` does not bear on the goal,
  // or null. Split HERE rather than at write time so every finding stays in the
  // row set: a verdict that was wrong is recoverable, and the appendix below
  // carries the reason so the filter is arguable rather than silent.
  //
  // Length-guarded like the extras: a mismatch means the two lists are not the
  // same sequence, and setting aside the WRONG finding is far worse than
  // setting none aside.
  const framework = (run.prioritisation?.plan?.framework || "").trim()
  const frameworkReason = (run.prioritisation?.plan?.framework_reason || "").trim()
  const isMoscowFramework = framework.toLowerCase() === "moscow"
  // THE HEADING IS SAID, NOT THE STORED ENUM. `framework` above is the
  // storage/comparison value ("rice", "moscow"); this is what a reader is
  // shown ("RICE", "MoSCoW"). `framework` itself stays raw for the
  // comparisons above and below — only the two headings render this.
  const frameworkLabel = frameworkDisplayName(framework)
  const accountValue = Number(run.prioritisation?.plan?.account_value ?? 0) || 0
  const asideRaw = run.prioritisation?.set_aside_by_rank
  const findingsExtra = run.prioritisation?.findings_extra_by_rank
  const allFindings = (() => {
    const base = run.findings ?? []
    if (!Array.isArray(findingsExtra) || findingsExtra.length !== base.length) {
      return base
    }
    return base.map((f, i) => {
      const x = (findingsExtra[i] ?? {}) as Record<string, unknown>
      const kept = Object.fromEntries(
        Object.entries(x).filter(([, v]) => Boolean(v)),
      )
      return { ...f, ...kept } as GoalFinding
    })
  })()
  const asideReasons: (string | null)[] =
    Array.isArray(asideRaw) && asideRaw.length === allFindings.length
      ? asideRaw
      : allFindings.map(() => null)
  const findings = allFindings.filter((_, i) => !asideReasons[i])
  const setAside = allFindings
    .map((f, i) => [f, asideReasons[i]] as const)
    .filter(([, r]) => Boolean(r))

  const headline = findings[0]
  // THE PANEL IS A SECOND RENDERER OF THE SAME REPORT, and it is the one a
  // reader actually looks at — `report.py` renders the exported/editable
  // document, this renders the right panel. Fixing only the server left the
  // panel printing "It is the largest thing this reading found: Could not be
  // sized", which is both halves of the same sentence contradicting each
  // other. Every honesty rule in `_headline_section` therefore has to exist
  // here too, keyed on the same two facts.
  // ONE FACT ABOUT THE CORPUS, OR MANY ABOUT THE FINDINGS? When every row
  // carries the SAME weakest link or cap, it is the former — a corpus with no
  // outcome evidence anywhere gives all 32 findings an identical sentence, and
  // printing it 32 times reads as 32 separate judgements. Detected, never
  // assumed: two different values and both go back on their own rows.
  // THE SAME RULE FOR ASSUMED PARAMETERS. I8 says disclose the assumption
  // where the number is read; it does not say disclose it 279 times. On a
  // corpus with no revenue connected every finding carries the identical
  // "value_per_account: no revenue data connected; accounts weighted equally",
  // and a real report printed it on all 279. Mirrors `_shared_assumptions`.
  const assumptionKey = (f: GoalFinding): string =>
    JSON.stringify(
      (f.assumed_params ?? [])
        .map((p) => [(p.name ?? "").trim(), (p.basis ?? "").trim()])
        .sort(),
    )
  // FINDINGS WITH NO ASSUMPTION ARE NOT COUNTED AGAINST THE MATCH. Asking
  // whether EVERY finding carried the identical set sounded right and never
  // fired: a live run had 326 findings, 30 sized and carrying
  // `value_per_account`, 296 unsized and carrying nothing. An unsized finding
  // has no size to qualify, so it has no assumption — that is not
  // disagreement. Mirrors `_shared_assumptions` in `report.py`.
  const carriers = findings.filter((f) => (f.assumed_params ?? []).length)
  const sharedAssumptions: GoalFinding["assumed_params"] | undefined =
    carriers.length >= 2 &&
    new Set(carriers.map(assumptionKey)).size === 1
      ? carriers[0].assumed_params
      : undefined

  const sharedReason = (key: "weakest_leg_reason" | "cap_reason"): string => {
    if (findings.length < 2) return ""
    const vals = new Set(
      findings.map((f) => (f.confidence?.[key] ?? "").trim()),
    )
    if (vals.size !== 1) return ""
    const only = [...vals][0]
    return only
  }
  // Same rule for the ledger: one distinct reason across MORE THAN ONE
  // rejection is a statement about the corpus, not about each candidate.
  // GROUPED BY REASON, because that is the shape of the answer. A real run
  // rejected 102 candidates for FIVE distinct reasons — 49 with no
  // authoritative source, 47 backed by a single claim, 4 from one account —
  // and the flat list printed each reason beside each label, so the same
  // sentence appeared 49 times and a reader could not see that half the ledger
  // died one way and half another without counting by hand.
  //
  // Biggest cause first, ties broken on the reason text, so a re-render of the
  // same run produces the same document — the engine is deterministic
  // everywhere else and a section that reordered itself would undercut that.
  // BOOKKEEPING IS NOT A CANDIDATE. Two ledger rows stand for everything the
  // list could NOT hold — the "N further candidates" overflow summary and the
  // one for signals with no usable embedding. Counted as rejections they made
  // a run that considered 1,576 candidates report "(102)"; grouped as reasons
  // they turned a one-cause ledger into three. Kept, but as their own facts.
  const AGGREGATE_STAGES = new Set(["overflow", "ungrouped"])
  const ruledOutAggregates = (run.considered ?? []).filter((r) =>
    AGGREGATE_STAGES.has((r.stopped_at_stage ?? "").trim()))
  const ruledOutCandidates = (run.considered ?? []).filter((r) =>
    !AGGREGATE_STAGES.has((r.stopped_at_stage ?? "").trim()))
  const ruledOutGroups = (() => {
    const rows = ruledOutCandidates
    const by = new Map<string, typeof rows>()
    for (const r of rows) {
      const key = (r.reason ?? "").trim()
      const bucket = by.get(key)
      if (bucket) bucket.push(r)
      else by.set(key, [r])
    }
    return [...by.entries()].sort(
      (a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]),
    )
  })()
  // One band across more than one finding means the ordering's last term is
  // invisible to the reader — see the lede below.
  const oneBand =
    findings.length > 1 &&
    new Set(findings.map((f) => (f.confidence_band ?? "").trim())).size === 1
  const sharedWeakest = sharedReason("weakest_leg_reason")
  const sharedCap = sharedReason("cap_reason")

  // THE ALTERNATIVES, NUMBERED, AND THE GAPS UNDER THE ONE BEING
  // RECOMMENDED. Both are deterministic reads over what the engine already
  // produced — no model call, no grouping, nothing chosen (I2) — and both
  // mirror `report.py`'s `_findings_section` exactly, so the panel and the
  // exported document cannot label the same run differently.
  const options = optionNumbers(findings)
  const [recommendedGapsIndex, recommendedGaps] = dataGapsFor(findings)
  // ONE TOPIC NAMED TWICE IS NOT TWO OPTIONS. When the engine's own
  // `sameTopic` says the top two write-ups are the same subject, the Option
  // labels come off and the recommended card explains the absence instead of
  // comparing against a sibling that is not an alternative. Presentation only
  // — findings, ranking and binding are untouched. Mirrors `report.py`.
  const oneTopic = optionsAreOneTopic(findings)
  const optionTotal = options.length ? Math.max(...options) : 0
  // STATED ONLY WHEN THE CORPUS HAS MORE THAN ONE BUCKET. On a run of nothing
  // but blockers the claim-type term did no work, and saying it ordered the
  // list would be an overstatement in the other direction. Mirrors
  // `report.py`'s `_findings_section`.
  const bucketClause =
    new Set(
      findings.map((f) => typeBucket((f as { claim_types?: string[] }).claim_types ?? [])),
    ).size > 1
      ? "What blocks an account is placed above what an account only asks for, whatever their sizes. "
      : ""
  const unsized = findings.filter((f) => f.impact_value == null).length
  const anythingSized = unsized < findings.length
  // HOW MUCH OF THE UNSIZED DISCLOSURE THE HEADLINE ALREADY MADE. Mirrors
  // `_headline_unsized_coverage` in `report.py` exactly, because the panel and
  // the document render the same run and a reader compares them.
  //
  // Three states, not two, and the third is why this is not a boolean: the
  // branch with an UNSIZED top row states the caveat and NEVER NAMES THE
  // COUNT, so treating it as "covered" drops "N of them could not be sized"
  // out of the page entirely — de-duplication turning into deletion.
  const headlineCovers: "full" | "caveat" | "none" =
    !findings.length || !unsized
      ? "none"
      : findings[0].impact_value != null
        ? "full"
        : anythingSized
          ? "caveat"
          : "none"
  const topIsConflict = headline?.adjudication === "conflict"
  // Confidence and claim count read the same in every branch, so they are
  // built once rather than repeated four times and drifting.
  const lead = `${
    headline?.confidence_band ? `, at ${headline.confidence_band} confidence` : ""
  }${
    headline?.claim_ids?.length
      ? `, resting on ${headline.claim_ids.length} claim${
          headline.claim_ids.length === 1 ? "" : "s"
        }`
      : ""
  }`
  const definition =
    plan?.definition_text || run.prioritisation?.proposed_definition || ""
  const excluded = plan?.excluded_sources ?? []
  const hypotheses = plan?.hypotheses ?? []
  const gaps = plan?.cannot_answer ?? []
  const notes = run.coverage_notes ?? []

  // ── THE MEMO'S RUNNING ORDER, IN THE READER'S OWN WORDS. ────────────────
  //
  //   "finding number one could be, hey, maybe build XYZ … and then we go to
  //    item number two … but I think you should do number one because it's
  //    the most important one … so this is the RICE prioritization, with the
  //    table. And then the bottom will be other things that we considered."
  //
  // Two write-ups, why the first one, the table, the tail — and everything
  // describing HOW the run worked below all of it, under "how this was
  // produced". Mirrors `render_report_html`'s assembly order exactly.
  //
  // NOTHING HERE CHANGES WHAT THE ENGINE COMPUTED: same findings, same frozen
  // rank order (I10), same values. This is the order they are read in.
  const written = findings.slice(0, MAX_WRITTEN_UP_FINDINGS)
  const otherConsidered = findings.slice(MAX_WRITTEN_UP_FINDINGS)
  const otherListed = otherConsidered.slice(0, MAX_OTHER_CONSIDERED_ROWS)
  const otherBeyond = otherConsidered.length - otherListed.length
  // The comparison reads after BOTH write-ups when there are two; with one
  // there is no "next" to read it after, so it stays on the card.
  const deferComparison = written.length > 1
  const comparison = deferComparison
    ? (written.map((f) => (f.deep_recommendation?.comparison || "").trim())
        .find(Boolean) || "")
    : ""
  // ONE CALL-COUNT FLOOR NOTE PER DOCUMENT — it is a fact about how the corpus
  // was ingested, not a judgement about any one finding, and it used to print
  // under every card that showed a call count.
  const callNoteIndex = written.findIndex((f) => hasCallCount(f.surfaced_by ?? []))
  const setAsideShown = setAside.slice(0, MAX_SET_ASIDE_ROWS)
  // The ranking table's rows, and the scale its reach bars are drawn against
  // — the widest reach among the rows actually rendered, so a bar is read
  // against its own table. An unsized row contributes nothing and draws
  // nothing (I3).
  const rankRows = findings.slice(0, isMoscowFramework ? MAX_MOSCOW_ROWS : MAX_RICE_ROWS)
  const rankLargest = largestReach(rankRows)
  // The funnel's three theme stages share one scale. Signals are NOT on it:
  // thousands of signals cluster into tens of themes, so putting both on one
  // axis would draw a full-width bar beside three single cells and hide the
  // step the reader needs. The signal count leads the funnel as the number it
  // is. Mirrors `report.py`'s `_funnelChart` reasoning.
  const funnelStages: [string, number][] = ([
    ["Themes found", allFindings.length],
    ["Bear on this goal", findings.length],
    ["Written up here", written.length],
  ] as [string, number][]).filter(([, v]) => v > 0)
  const funnelLargest = funnelStages.reduce((n, [, v]) => Math.max(n, v), 0)
  // THE PROBLEM STATEMENT'S INPUTS — every one a read of something the
  // engine already produced. The KIND of claim behind the top finding is the
  // whole of whether there is a problem at all: a blocker reads differently
  // from a preference, and a theme that merely describes the world is neither.
  // No model is called and no narrative is invented (I2). Mirrors
  // `report.py`'s `_problem_section`.
  const topBucket = headline
    ? typeBucket((headline as { claim_types?: string[] }).claim_types ?? [])
    : null
  const topSized = headline ? headline.impact_value != null : false
  // WHEN THE CORPUS DOES NOT SUPPORT A PROBLEM STATEMENT, SAY SO. A theme
  // that neither blocks nor is asked for AND could not be sized is not a
  // problem; manufacturing urgency for it is worse than saying nothing, on a
  // product whose entire claim is that it does not overstate.
  const noProblemStated =
    topBucket !== TYPE_BUCKET_BLOCKER
    && topBucket !== TYPE_BUCKET_PREFERENCE
    && !topSized
  const topClaims = headline?.claim_ids?.length ?? 0
  const topSources = (headline?.surfaced_by ?? []).filter(Boolean).length
  const topBecause = (
    (headline?.deep_recommendation?.because || "").trim()
    || (headline?.recommendation?.because || "").trim()
  )
  const decisionOwner = (plan?.decision_owner || "").trim()
  const neededBy = (plan?.needed_by || "").trim()
  const decisionReach = findings
    .filter((f) => f.impact_value != null)
    .reduce((n, f) => n + (f.impact_value ?? 0), 0)
  const conflictClause =
    `An authoritative disagreement is placed above ${bucketClause ? "both" : "all of it"}`
    + ": two sources that may both speak contradicting each other is worth"
    + " more than either alone."

  return (
    <article className="ga-doc" data-testid="goal-report">
      <header className="ga-doc-header">
        <p className="ga-doc-eyebrow">Goal analysis</p>
        <h1 className="ga-doc-title">{run.goal_text}</h1>
        {editable ? (
          <div className="ga-doc-actions" data-testid="goal-report-actions">
            <button
              type="button"
              className="ga-doc-action"
              data-testid="goal-report-edit"
              disabled={busy}
              onClick={onEdit}
            >
              Edit
            </button>
            <button
              type="button"
              className="ga-doc-action"
              data-testid="goal-report-save-copy"
              disabled={busy}
              onClick={onSaveCopy}
            >
              Save as document
            </button>
            {/* SAID BEFORE THE CLICK, not after it. Editing is not a mode you
                can back out of — it detaches the report from the run for good
                — and a reader who did not know that would be told only once it
                had happened. */}
            <p className="ga-doc-actions-note">
              Editing keeps the analysis exactly as it is and turns this report
              into a document you own. It stops updating from the run.
            </p>
          </div>
        ) : null}
      </header>

      {/* ── THE HEADLINE NUMBERS, ON ONE LINE, AT THE TOP. ────────────────
          Reading the current output the customer put the line exactly here:
          the content started at "the short version", and everything above it
          was, in his words, information that "should not be in the final
          report". So the document opens with the title, the numbers, and the
          answer. NO DATA WINDOW cell: claim dates on this substrate are the
          INGEST clock, so a window printed from them would be when we read
          the evidence wearing the clothes of the period it covers. */}
      {allFindings.length ? (
        <section className="ga-doc-section" data-testid="goal-strip">
          <div className="ga-strip">
            {([
              ["Signals read", (plan?.total_signals ?? 0).toLocaleString()],
              ["Themes found", allFindings.length.toLocaleString()],
              ["Bear on this goal", findings.length.toLocaleString()],
              ["Sized", findings.filter((f) => f.impact_value != null).length.toLocaleString()],
              ["High confidence",
               findings.filter((f) => f.confidence_band === "high").length.toLocaleString()],
              // NOT "with A recommendation" — that read as the same count the
              // recommendation-basis note makes, which counts only the DEEP
              // pass. This cell counts flat OR deep — the union, mirroring
              // `report.py`'s `_stat_strip` — so a run can show 8 here and 2
              // there, both true, about two different senses of the word.
              ...(findings.some((f) => f.recommendation?.action || f.deep_recommendation?.action)
                ? [["Flagged with any suggestion",
                    findings.filter((f) => f.recommendation?.action || f.deep_recommendation?.action)
                      .length.toLocaleString()]]
                : []),
              // LABELLED IN THE CELL. A number in a strip reads as a fact, and
              // this one is the reader's own estimate multiplied out.
              ...(accountValue > 0 && findings.some((f) => f.impact_value != null)
                ? [["Reach × your estimate",
                    Math.round(findings.reduce((n, f) => n + (f.impact_value ?? 0), 0)
                      * accountValue).toLocaleString()]]
                : []),
            ] as [string, string][]).map(([label, value]) => (
              <div key={label} className="ga-strip-cell">
                <strong>{value}</strong>
                <span>{label}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {/* ── THE FUNNEL, AS A SHAPE. ───────────────────────────────────────
          Every number here already existed in the document; what did not was
          the narrowing, which was three paragraphs in three different
          sections. Every bar carries its own number, and a stage with nothing
          in it is omitted rather than drawn at zero. */}
      {funnelStages.length > 1 ? (
        <section className="ga-doc-section" data-testid="goal-funnel-chart">
          <table className={styles.chart}>
            <tbody>
              {plan?.total_signals ? (
                <tr>
                  <td>Signals read</td>
                  <td />
                  <td><strong>{plan.total_signals.toLocaleString()}</strong></td>
                </tr>
              ) : null}
              {funnelStages.map(([label, value]) => (
                <tr key={label}>
                  <td>{label}</td>
                  <td><Bar value={value} largest={funnelLargest} /></td>
                  <td><strong>{value.toLocaleString()}</strong></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {/* ── THE ASK, IN THREE LINES. The reference plan opens with exactly
          this — "Your question: increase revenue by 2%" — and a memo that
          does not restate its own question makes a reader hold it in their
          head while reading the answer. SHORT ON PURPOSE: the goal, the
          confirmed definition verbatim, and the one line saying what the
          definition is for. What the definition did and did not DECIDE is
          method and reads in the appendix. */}
      <section className="ga-doc-section" data-testid="goal-definition">
        <h2 className="ga-doc-h2">The ask</h2>
        {run.goal_text ? (
          <p className="ga-doc-note">
            <strong>Your question.</strong> {run.goal_text}
          </p>
        ) : null}
        {definition ? (
          <>
            <p className="ga-doc-lede">
              You confirmed this means, in your own words:
            </p>
            <blockquote className="ga-doc-quote">{definition}</blockquote>
            <p className="ga-doc-note" data-testid="goal-definition-note">
              Recorded here so a decision can be defended against it.
            </p>
          </>
        ) : (
          // Stated, not skipped. A report with no recorded definition is a
          // report whose subject is unknown, and hiding that would make it
          // look like the ordinary case.
          <p className="ga-doc-note" data-testid="goal-no-definition">
            No confirmed definition was recorded for this run, so what the goal
            means is not on the record. Read what follows as being about the
            goal as typed, nothing narrower.
          </p>
        )}
      </section>

      {/* ── THE PROBLEM: the one paragraph that says why this matters rather
          than what we found. ASSEMBLED, NEVER AUTHORED — the top-ranked
          finding's own label, the kind of claim behind it, the reach and
          claim and source counts it actually carries, and its own "why".
          AND IT REFUSES TO OVERSTATE: with no blocker at the top and nothing
          sized it says what the corpus does support, in one line. Mirrors
          `report.py`'s `_problem_section`. */}
      {headline ? (
        <section className="ga-doc-section" data-testid="goal-problem">
          <h2 className="ga-doc-h2">The problem</h2>
          {noProblemStated ? (
            <p className={`ga-doc-note ${styles.measure}`}>
              The evidence does not state a problem here: the strongest theme
              describes the world rather than blocking or asking for anything,
              and it could not be sized. What follows is what was found, not a
              case for acting on it.
            </p>
          ) : (
            <>
              <p className={styles.problemClaim}>
                {(headline.label || "").trim() || headline.statement}
              </p>
              <p className={`ga-doc-note ${styles.measure}`}>
                {topBucket === TYPE_BUCKET_BLOCKER ? (
                  <>
                    The evidence has this blocking accounts today, not as
                    something they would merely prefer.
                  </>
                ) : topBucket === TYPE_BUCKET_PREFERENCE ? (
                  <>
                    Accounts are asking for this. Nothing in the evidence says
                    they are blocked by it.
                  </>
                ) : (
                  <>
                    The evidence describes this rather than asking for or
                    blocking anything, so read it as context rather than as
                    something stopping you.
                  </>
                )}{" "}
                Measured at: <Sized f={headline} idPrefix="goal-problem" />
                {topClaims ? ` · ${topClaims} claim${topClaims === 1 ? "" : "s"}` : ""}
                {topSources
                  ? ` · ${topSources} source document${topSources === 1 ? "" : "s"}`
                  : ""}
                .
                {/* I3, again and for the same reason: a missing size is not a
                    small one, and a problem statement is exactly where that
                    would be read as one. */}
                {!topSized ? (
                  <>
                    {" "}A missing size is not a small one: how many accounts
                    this touches is unknown, not zero.
                  </>
                ) : null}
              </p>
              {/* THE FINDING'S OWN REASONING, quoted from the finding rather
                  than restated, so this paragraph and the write-up below it
                  cannot say two different things. */}
              {topBecause ? (
                <p className={`ga-doc-note ${styles.measure}`}>
                  {stripClaimRefs(topBecause)}
                </p>
              ) : null}
            </>
          )}
        </section>
      ) : null}

      {/* ── THE SHORT VERSION. Where the customer said the content starts. */}
      <section className="ga-doc-section" data-testid="goal-headline">
        <h2 className="ga-doc-h2">The short version</h2>
        {headline ? (
          <>
            <p className="ga-doc-headline">{headline.statement}</p>
            <p className="ga-doc-note" data-testid="goal-headline-note">
              {topIsConflict ? (
                <>
                  It is placed first because two sources that may both speak
                  contradict each other{lead}. That placement is a rule, not a
                  measurement — a disagreement is placed above every finding
                  that is not one, so read it as the disagreement most worth
                  resolving rather than as the biggest thing here.
                </>
              ) : headline.impact_value != null && unsized === 0 ? (
                <>
                  It is the largest thing this reading found:{" "}
                  <Sized f={headline} idPrefix="goal-headline" />
                  {lead}. Largest by how much of your book it touches — not by
                  how much it would move the metric, which this reading cannot
                  compute.
                </>
              ) : headline.impact_value != null ? (
                <>
                  It is the largest of the ones that could be sized:{" "}
                  <Sized f={headline} idPrefix="goal-headline" />
                  {lead}.{" "}
                  {unsized === 1 ? "One of these" : `${unsized} of these`} could
                  not be sized at all, and a missing size is not a small one —
                  so this is the largest known size, not necessarily the largest
                  thing here.
                </>
              ) : anythingSized ? (
                <>
                  It is listed first{lead}. It could not be sized, though others
                  below it could — a missing size is not a small one, so do not
                  read its position as a measurement of it.
                </>
              ) : (
                <>
                  It is listed first{lead}. Nothing in this reading could be
                  sized, so what orders these is the kind of claim behind each
                  one — what blocks an account above what an account only asks
                  for — with how sure we are breaking ties inside a kind.
                </>
              )}
            </p>
          </>
        ) : (
          <p className="ga-empty">
            Nothing survived verification. What was considered is listed below
            with the reason it was dropped — that list, not this silence, is the
            result of this run. Where more was considered than the list can
            hold, the remainder is counted with it rather than folded in as
            though it were one more candidate.
          </p>
        )}
      </section>

      {/* ── THE DECISION: who signs off, by when, and what is at stake.
          ONLY WHAT WAS ANSWERED — a reader who skipped the plan gate's
          questions gets no box rather than a box of blanks, because a
          decision box with an empty owner implies the decision has a home
          when it does not. WHAT IS AT STAKE IS DERIVED, NOT ASSERTED: this
          corpus cannot forecast, so the line states what the evidence COUNTS.
          Mirrors `report.py`'s `_decision_section`, which the panel did not
          render at all until the memo restructure put the decision at the
          top. */}
      {decisionOwner || neededBy ? (
        <section className="ga-doc-section" data-testid="goal-decision">
          <h2 className="ga-doc-h2">The decision</h2>
          <p className="ga-doc-note">
            {decisionOwner ? <><strong>Owner</strong> {decisionOwner}</> : null}
            {decisionOwner && neededBy ? " · " : null}
            {neededBy ? <><strong>Needed by</strong> {neededBy}</> : null}
          </p>
          {decisionReach > 0 ? (
            <p className="ga-doc-note">
              The findings that bear on this goal touch{" "}
              <strong>{decisionReach.toLocaleString()} accounts</strong>
              {accountValue > 0 ? (
                <>
                  {" "}— about{" "}
                  {Math.round(decisionReach * accountValue).toLocaleString()} on
                  your own figure of {accountValue.toLocaleString()} per
                  account, which is an estimate you gave rather than something
                  measured
                </>
              ) : null}
              . That is what the evidence counts, not a forecast of what changes
              if you act.
            </p>
          ) : null}
        </section>
      ) : null}

      {/* THE ONE RECOMMENDATION FOR THE WHOLE REPORT — narrated across the
          per-finding deep recommendations below, never a replacement for
          them. Silent when there was nothing to synthesize. KEPT IN ITS OWN
          SECTION, never merged with the money footnotes: one is a narrated
          recommendation, the others are dollar-bearing, and a reader should
          never be tempted to read one as informing the other. */}
      {synthesizedAction && synthesizedBecause ? (
        <section className="ga-doc-section" data-testid="goal-synthesized-recommendation">
          <h2 className="ga-doc-h2">The recommendation</h2>
          <p className="ga-doc-note">
            <strong>Recommended.</strong> {synthesizedAction}
          </p>
          <p className="ga-doc-note">
            <em>Why.</em> {synthesizedBecause}
          </p>
          {synthesizedCitations.length ? (
            <>
              <p className="ga-doc-note"><strong>Drawn from.</strong></p>
              <ul className="ga-assumed" data-testid="goal-synthesized-citations">
                {synthesizedCitations.map((c, i) => (
                  <li key={i}>
                    {c.evidence} <em>— from: “{c.cited_claim}”</em>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </section>
      ) : null}

      {/* ── WHAT TO DO: the two write-ups. ───────────────────────────────── */}
      {findings.length ? (
        <section className="ga-doc-section">
          <h2 className="ga-doc-h2">What to do</h2>
          {/* HOW MUCH OF THE UNSIZED DISCLOSURE THE HEADLINE ALREADY MADE —
              three states, not two: the middle one states the caveat and
              never names the count, so treating it as covered would drop
              "257 of them could not be sized" out of the page entirely. */}
          {unsized && headlineCovers !== "full" ? (
            <p className={`ga-doc-lede ${styles.measure}`} data-testid="goal-findings-lede">
              {unsized === 1 ? "One" : unsized} of these could not be sized
              {headlineCovers === "caveat" ? (
                "."
              ) : (
                <>
                  {" "}— an unsized theme sorts last without being small: its
                  size is unknown, not zero.
                </>
              )}
            </p>
          ) : null}
          {sharedWeakest ? (
            <p className="ga-doc-note" data-testid="goal-shared-weakest">
              <strong>Every finding has the same weakest link</strong>, stated
              once rather than on each: {sharedWeakest}
              {/* The cap arrives uncapitalised ("capped at medium: …"), so a
                  full stop before it rendered "…are not. capped at medium". */}
              {sharedCap ? `; ${sharedCap}.` : "."}
            </p>
          ) : sharedCap ? (
            <p className="ga-doc-note" data-testid="goal-shared-cap">
              <strong>Every finding is capped the same way</strong>, stated
              once rather than on each: {sharedCap}.
            </p>
          ) : null}
          {sharedAssumptions?.length ? (
            <div className="ga-doc-note" data-testid="goal-shared-assumptions">
              <p>
                {/* SAYS HOW MANY IT SPEAKS FOR. "Every finding" is false when
                    only the sized ones carry an assumption, and a hoisted
                    sentence that overstates its scope is worse than the
                    repetition it replaced. */}
                <strong>
                  {carriers.length === findings.length
                    ? "Every finding rests on the same assumption"
                    : `${carriers.length} findings rest on the same assumption`}
                  {sharedAssumptions.length > 1 ? "s" : ""}
                </strong>
                , stated once rather than on each:
              </p>
              <ul className="ga-assumed">
                {sharedAssumptions.map((p) => (
                  <li key={p.name}>
                    <b>{p.name}</b>: {p.basis}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {/* A RANGE, IN ITS OWN PARAGRAPH, NEVER BESIDE A COMMITTED FIGURE.
              One is a sum of money people agreed to; the other is a rate card
              quoted to whoever asked, whose total is meaningless — a $30,000
              tier quoted sixteen times is not $480,000. */}
          {listPricingBasis ? (
            <p className="ga-doc-note" data-testid="goal-list-pricing-basis">
              {listPricingBasis}
            </p>
          ) : null}
          <ol className="ga-doc-findings">
            {written.map((f, i) => (
              <ReportFinding
                key={f.id}
                f={f}
                rank={i + 1}
                sharedWeakest={!!sharedWeakest}
                sharedCap={!!sharedCap}
                sharedAssumptions={!!sharedAssumptions?.length}
                option={options[i]}
                dataGaps={
                  i === recommendedGapsIndex ? recommendedGaps : EMPTY_GAPS
                }
                oneTopic={oneTopic}
                optionTotal={optionTotal}
                oneTopicNote={
                  oneTopic && i === recommendedGapsIndex ? ONE_TOPIC_NOTE : ""
                }
                deferComparison={deferComparison}
                deferGaps
                showCallNote={i === callNoteIndex}
              />
            ))}
          </ol>
        </section>
      ) : null}

      {/* ── WHY NUMBER ONE. "But I think you should do number one because
          it's the most important one" — the comparison, read AFTER both
          write-ups rather than inside the first. The sentence itself is
          unchanged and still comes off the engine's own
          `deep_recommendation.comparison`; only where it sits has moved. */}
      {written.length > 1 && (comparison || oneTopic) ? (
        <section className="ga-doc-section" data-testid="goal-why-number-one">
          <h2 className="ga-doc-h2">Why number one</h2>
          {comparison ? (
            <p className="ga-doc-note" data-testid="goal-finding-comparison">
              {comparison}
            </p>
          ) : null}
          {oneTopic ? (
            <p className="ga-doc-note" data-testid="goal-finding-one-topic">
              <b>Why these are not two options.</b> {ONE_TOPIC_NOTE}
            </p>
          ) : null}
        </section>
      ) : null}

      {/* ── BEFORE YOU SPEND: what is not known about the thing being
          recommended. The same list the recommended card used to print
          inside itself, lifted out now that the memo runs two write-ups
          instead of ten — it qualifies the recommendation the document is
          making, not one card among many. GAPS, NOT ACTIONS: the heading
          says "before you spend" rather than "next steps" precisely so it
          cannot be read as work competing with the recommendation above it.
          Corpus-level gaps render once, at the end. */}
      {recommendedGaps.length ? (
        <section className="ga-doc-section" data-testid="goal-finding-data-gaps">
          <h2 className="ga-doc-h2">Before you spend</h2>
          <p className={`ga-doc-note ${styles.measure}`}>
            Gaps in what is known about the recommended option, not work to
            schedule.
          </p>
          <ul className={styles.evList}>
            {recommendedGaps.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* ── THE RANKING, WITH REACH BARS. ────────────────────────────────
          The table NEVER re-sorts: `_rank` froze the order before any of this
          ran, and a scoring table that reordered would be the prioritisation
          step mutating the ranking (I10). How its terms are DEFINED is method
          and reads under "how this was produced"; the table itself is part of
          the memo, where the customer put it: "so this is the RICE
          prioritization, with the table." */}
      {framework && findings.length && !isMoscowFramework ? (
        <section className="ga-doc-section" data-testid="goal-rice">
          <h2 className="ga-doc-h2">The ranking ({frameworkLabel})</h2>
          <div className="ga-rice-scroll">
            <table className="ga-rice">
              <thead>
                <tr>
                  <th>Theme</th><th>Reach</th><th>Impact</th>
                  <th>Confidence</th><th>Effort</th><th>Score</th><th>Inputs</th>
                </tr>
              </thead>
              <tbody>
                {rankRows.map((f) => {
                  const r = riceFor(f)
                  return (
                    <tr key={f.id}>
                      <td>{r.label}</td>
                      {/* NOT "—", AND NEVER A ZERO-LENGTH BAR (I3): an
                          unmeasured theme and a measured-and-tiny one lead to
                          opposite decisions. */}
                      <td>
                        {r.reach === null ? "Not measured" : (
                          <>
                            {r.reach} {r.reachUnit}
                            <span className={styles.reachBar}>
                              <Bar value={r.reach} largest={rankLargest} />
                            </span>
                          </>
                        )}
                      </td>
                      <td>{r.impact}</td>
                      <td>{r.confidenceBand}</td>
                      <td>{EFFORT_ABSENT}</td>
                      <td>{r.score === null ? "—" : r.score.toFixed(1)}</td>
                      <td>{r.inputsPresent} of {RICE_INPUT_COUNT}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {/* NO SILENT CAPS. */}
          {findings.length > MAX_RICE_ROWS ? (
            <p className="ga-doc-note">
              The other {findings.length - MAX_RICE_ROWS} are listed below, not
              scored out here — a table this long stops being one.
            </p>
          ) : null}
        </section>
      ) : null}

      {/* MoSCoW is what this run picked when nothing connected carries a
          number — RICE's Reach and Impact would both come back unmeasured on
          every row rather than ranking anything. Same non-reordering
          discipline as the RICE table above (I10). */}
      {framework && findings.length && isMoscowFramework ? (
        <section className="ga-doc-section" data-testid="goal-moscow">
          <h2 className="ga-doc-h2">The ranking ({frameworkLabel})</h2>
          <div className="ga-rice-scroll">
            <table className="ga-rice">
              <thead>
                <tr>
                  <th>Theme</th><th>Bucket</th><th>Why</th><th>Reach</th>
                  <th>Source documents</th>
                </tr>
              </thead>
              <tbody>
                {rankRows.map((f) => {
                  const r = moscowFor(f)
                  return (
                    <tr key={f.id}>
                      <td>{r.label}</td>
                      <td>{r.bucket}</td>
                      <td>{r.bucketBasis}</td>
                      <td>
                        {r.reach === null ? "Not measured" : (
                          <>
                            {r.reach} {r.reachUnit}
                            <span className={styles.reachBar}>
                              <Bar value={r.reach} largest={rankLargest} />
                            </span>
                          </>
                        )}
                      </td>
                      <td>{r.docCount}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {findings.length > MAX_MOSCOW_ROWS ? (
            <p className="ga-doc-note">
              The other {findings.length - MAX_MOSCOW_ROWS} are listed below,
              not bucketed out here — a table this long stops being one.
            </p>
          ) : null}
        </section>
      ) : null}

      {/* ── OTHER THINGS CONSIDERED. "And then the bottom will be other
          things that we considered — these are 20 other things that you could
          also build." One line each, in the run's own rank order, with
          everything past the cap COUNTED: a list that stops without saying so
          reads as the complete set, which is the silent degradation this
          feature exists to prevent. */}
      {otherConsidered.length ? (
        <section className="ga-doc-section" data-testid="goal-findings-overflow">
          <h2 className="ga-doc-h2">
            Other things considered ({otherConsidered.length})
          </h2>
          <p className="ga-doc-note">
            Ranked below the two above. One line each, in rank order — all of
            them are on the run.
          </p>
          <ul className="ga-doc-list">
            {otherListed.map((f, i) => (
              <li key={f.id} data-testid="goal-finding-overflow-row">
                {MAX_WRITTEN_UP_FINDINGS + i + 1}. {overflowStatement(f)}
              </li>
            ))}
          </ul>
          {otherBeyond > 0 ? (
            <p className="ga-doc-note">
              and {otherBeyond} more, all on the run.
            </p>
          ) : null}
        </section>
      ) : null}

      {hypotheses.length ? (
        <section className="ga-doc-section" data-testid="goal-hypotheses">
          <h2 className="ga-doc-h2">What you already believed</h2>
          <ul className="ga-doc-list">
            {hypotheses.map((h, i) => (
              <li key={i}>{h}</li>
            ))}
          </ul>
          {/* NOT a verdict. The engine does not test a stated hypothesis
              against the claims, and rendering these beside the findings
              without saying so would let a reader infer that silence meant
              "not supported" — a conclusion nothing produced. */}
          <p className="ga-doc-note">
            This reading did not test these. Nothing above was matched against
            what you wrote here, so their absence from the findings is not
            evidence against them.
          </p>
        </section>
      ) : null}

      {/* ── HOW THIS WAS PRODUCED. ────────────────────────────────────────
          Five sections that used to be the first five. Reading the current
          output the customer put the line at "the short version": everything
          above it — what this was asked to establish, what was read, the
          source breakdown, what was missing, how it was ranked — was, in his
          words, "all of this information should not be in the final report".

          MOVED, NEVER DELETED, and that distinction is the whole design.
          Several of these lines are the disclosures this feature is built on:
          that a run was filtered by a definition, that a third of the evidence
          was undated, that a ranking term could not be filled. A memo whose
          provenance has been deleted is not shorter, it is unfalsifiable.

          PER-FINDING DISCLOSURES DO NOT MOVE — an unsized value, an
          assumption behind one specific finding, the weakest link on one theme
          stay attached to the finding they qualify. */}
      <section
        className={`ga-doc-section ${styles.appendix}`}
        data-testid="goal-provenance"
      >
        <h2 className="ga-doc-h2">How this was produced</h2>
        <p className={`ga-doc-note ${styles.measure}`}>
          What the memo above rests on: what was read, what was missing from it,
          and how the ranking works.
        </p>

        <h3 className="ga-doc-h3">What was read</h3>
        {plan ? (
          <>
            <p className="ga-doc-lede">
              {plan.total_signals.toLocaleString()} signal
              {plan.total_signals === 1 ? "" : "s"} across{" "}
              {plan.sources.length} source
              {plan.sources.length === 1 ? "" : "s"}, listed separately because
              each witnesses different things.
            </p>
            <ul className="ga-doc-sources">
              {plan.sources.map((s) => (
                <li key={s.source_type} data-testid="goal-read-source">
                  <span className="ga-doc-source-count">
                    {s.signal_count.toLocaleString()}
                  </span>{" "}
                  <b>{s.label}</b> — {s.witnesses}
                </li>
              ))}
            </ul>
            {excluded.length ? (
              <p className="ga-doc-note" data-testid="goal-excluded">
                You excluded {excluded.map(humanSource).join(", ")} before this
                ran, so nothing above rests on it.
              </p>
            ) : null}
          </>
        ) : (
          <p className="ga-doc-note" data-testid="goal-no-plan">
            This run kept no record of which sources it read, so what is above
            cannot be checked against its own inputs.
          </p>
        )}
        {notes.length ? (
          <>
            <h4 className="ga-doc-h3">What was missing from it</h4>
            <ul className="ga-coverage" data-testid="goal-coverage">
              {notes.map((n, i) => (
                <li key={i}>
                  <b>{n.reason}</b> — {n.actual}
                </li>
              ))}
            </ul>
          </>
        ) : null}

        {/* THE FIRST THING A FILTERED LIST OWES ITS READER. Silent when
            nothing was set aside — a funnel with one step is not a funnel. */}
        {setAside.length ? (
          <div data-testid="goal-funnel">
            <h3 className="ga-doc-h3">What bears on this goal</h3>
            <p className="ga-doc-lede">
              <strong>
                {allFindings.length} themes were found. {findings.length} bear
                on this goal.
              </strong>{" "}
              The other {setAside.length} are listed with the reason each was
              set aside — not gone, and one set aside for this goal may be the
              answer to a different one.
            </p>
          </div>
        ) : null}
        {/* The relevance gate's disclosure half: the gate has a hard budget and
            a wall-clock deadline that can stop it early, and until this fired
            nothing ever said so. Separate from the funnel above, because this
            fires even when nothing was set aside. */}
        {relevanceJudged && relevanceJudged.considered > relevanceJudged.judged ? (
          <p className="ga-doc-note" data-testid="goal-relevance-coverage">
            Of the {relevanceJudged.considered} themes found, this run evaluated{" "}
            {relevanceJudged.judged} for relevance to your goal before its time
            or cost budget ran out. The other{" "}
            {relevanceJudged.considered - relevanceJudged.judged} are still
            counted as found and are kept in the list — unjudged, not
            irrelevant.
          </p>
        ) : null}

        {/* WHAT THE CONFIRMED DEFINITION DID AND DID NOT DECIDE. Claim
            SELECTION never sees it on either branch (`build_findings` runs
            with no goal argument). What changed when the relevance gate
            shipped is which findings a reader is SHOWN, and a run that ran
            the gate must not print the sentence denying it. `relevanceGateRan`
            is true only when the gate completed without raising — never
            guessed from whether anything was set aside, because a gate that
            judged everything true still ran. */}
        <h3 className="ga-doc-h3">What the definition decided</h3>
        {relevanceGateRan ? (
          <p className="ga-doc-note" data-testid="goal-definition-decided">
            Every theme was checked against your confirmed definition for
            whether it bears on the goal, and what did not is listed below with
            the reason. Nothing was SELECTED by it: a theme reaches that check
            because it is in the evidence you approved.
          </p>
        ) : (
          <p className="ga-doc-note" data-testid="goal-definition-decided">
            Nothing here was filtered or ranked by your definition — a theme
            appears because it is in the evidence you approved, not because it
            bears on what you asked about.
          </p>
        )}

        {/* HOW THE RANKING'S TERMS ARE DEFINED. RICE's letters carry
            assumptions this corpus cannot all satisfy, and a reader who
            assumes the standard ones will misread the table above. */}
        {framework && findings.length ? (
          <div data-testid="goal-ranking-legend">
            <h3 className="ga-doc-h3">How the ranking works ({frameworkLabel})</h3>
            {frameworkReason ? <p className="ga-doc-note">{frameworkReason}</p> : null}
            {isMoscowFramework ? (
              <ul className="ga-doc-note">
                <li><strong>MUST</strong> — a stated blocker: something is
                  stopping an account today. <em>Marked <strong>MUST?</strong>{" "}
                  when only one source document backs it.</em></li>
                <li><strong>SHOULD / COULD</strong> — a stated preference:
                  something an account asked for.</li>
                <li>Graded by how many <strong>independent source
                  documents</strong> back each one, not by raw claim count.</li>
              </ul>
            ) : (
              <>
                <ul className="ga-doc-note">
                  <li><strong>Reach</strong> — how many of your accounts the
                    theme touches. Counted, not estimated.</li>
                  <li><strong>Impact</strong> — how directly it bears on the
                    metric, read from the kind of claim behind it: something
                    blocked outranks something asked for, which outranks
                    something described.{" "}
                    <em>That ordering is ours, not your data&rsquo;s.</em></li>
                  <li><strong>Confidence</strong> — the band the evidence
                    earned.</li>
                  <li><strong>Effort</strong> — <em>{EFFORT_ABSENT}</em>.
                    Nothing connected carries a person-month, and inventing one
                    would put a number in front of you that no evidence
                    supports.</li>
                </ul>
                <p className="ga-doc-note">
                  With no effort anywhere, the score is reach × impact ×
                  confidence. An effort applied equally to every row divides
                  them all by the same number and cannot change their order.
                </p>
              </>
            )}
          </div>
        ) : null}

        {/* WHAT THE ORDER ACTUALLY IS. `_rank`'s key is (conflict, claim-type
            bucket, reach, confidence); each clause is stated only when the
            term it names did work on this run. */}
        {findings.length ? (
          <p className="ga-doc-note" data-testid="goal-ordering-note">
            {anythingSized ? (
              <>Ranked by reach — how many accounts each theme touches.{" "}</>
            ) : (
              <>Not ranked by reach: nothing here could be sized.{" "}</>
            )}
            {bucketClause}
            {conflictClause}
            {!anythingSized && oneBand ? (
              <>
                {" "}Within a kind, findings are ordered by a confidence score
                this report does not print, and every finding here carries the
                same band — so read the gap between two neighbours in one group
                as narrow.
              </>
            ) : null}
          </p>
        ) : null}

        {recommendationBasis ? (
          <p className="ga-doc-note" data-testid="goal-recommendation-basis">
            {/* CAPITALISED AND TERMINATED, because it follows a bold full stop
                and is the start of a sentence. `recommendationBasis` is
                authored as a clause — it also renders mid-sentence elsewhere. */}
            <strong>How many got a full recommendation.</strong>{" "}
            {stop(upperFirst(recommendationBasis))}
          </p>
        ) : null}

        {/* ── CONSIDERED AND SET ASIDE. NOT A DELETION: each of these was found,
            corroborated and ranked exactly like the findings above; what changed
            is that it does not answer the question that was asked. The reason
            beside each is what makes the filter arguable. CAPPED for the same
            reason the tail above is — at 95 rows this table was named as one of
            the things making the document unreadable — with the heading
            carrying the true total. */}
        {setAside.length ? (
          <div className="ga-doc-aside" data-testid="goal-set-aside">
            <h3 className="ga-doc-h3">
              Considered and set aside for this goal ({setAside.length})
            </h3>
            <p className="ga-doc-note">
              Found and ranked like the findings above. They are here because they
              do not bear on the goal as you defined it, not because the evidence
              was weak.
            </p>
            <div className="ga-rice-scroll">
              <table className="ga-rice">
                <thead>
                  <tr>
                    <th>Theme</th><th>What it is</th>
                    <th>Worth this cycle</th><th>Why it was set aside</th>
                  </tr>
                </thead>
                <tbody>
                  {setAsideShown.map(([f, reason]) => (
                    <tr key={f.id}>
                      <td><strong>{(f.label || "").trim() || f.statement}</strong></td>
                      <td>{(f.example || "").trim() || f.statement}</td>
                      {/* I3 as a vocabulary: never a misleading zero. */}
                      <td>{f.impact_value == null
                        ? "Unsized"
                        : `${f.impact_value} ${f.currency || "accounts"}`}</td>
                      <td>{reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {setAside.length > setAsideShown.length ? (
              <p className="ga-doc-note">
                and {setAside.length - setAsideShown.length} more set aside, all
                on the run.
              </p>
            ) : null}
          </div>
          ) : null}

        {/* SAID ONCE, AND SAID PLAINLY, because the memo above is read as
            though it named accounts and it does not. A finding carries how
            MANY accounts a theme touches; which ones is never stored. Stating
            the limit here is the alternative to a write-up implying a customer
            list it cannot produce. */}
        <p className="ga-doc-note" data-testid="goal-accounts-not-named">
          Accounts are counted, not named: this reading records how many
          accounts a theme touches, never which ones. Where a name appears it is
          a source document.
        </p>
      </section>

      {run.considered?.length ? (
        <section className="ga-doc-section" data-testid="goal-considered">
          {/* OPEN while the list is short, folded once it is long — but the
              COUNT is in the summary either way, so the ledger is never
              silently thin. */}
          <details open={run.considered.length <= RULED_OUT_OPEN_MAX}>
            <summary className="ga-doc-h2 ga-doc-summary">
              Considered and ruled out ({ruledOutCandidates.length})
            </summary>
            {/* WHAT KILLED THEM, not just that something did. A real run had
                102 rejections and FIVE causes, half of them one rule and half
                another, which a flat list made invisible without counting by
                hand. The counts are the actionable part. */}
            <p className="ga-doc-lede">
              A ranking whose rejections are invisible is a ranking you have to
              take on faith. Each of these was a candidate and each one died for
              a stated reason
              {ruledOutGroups.length > 1 ? (
                <>
                  , grouped below by that reason — {ruledOutGroups.length} of
                  them across {ruledOutCandidates.length} candidates.
                </>
              ) : (
                <>, and every one of them died for the same one.</>
              )}
            </p>
            {ruledOutGroups.map(([reason, rows]) => (
              <div key={reason} data-testid="goal-ruled-out-group">
                <p className="ga-doc-note">
                  <b>{rows.length}</b>{" "}
                  {reason ? <>died because {reason}</> : <>died with no reason recorded</>}
                </p>
                <ul className="ga-doc-ruled-out">
                  {rows.map((r) => (
                    <li key={r.id}>
                      <b>{r.label}</b>
                      {r.stopped_at_stage ? (
                        <em> (stopped at {r.stopped_at_stage})</em>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            {ruledOutAggregates.map((r) => (
              <p className="ga-doc-note" key={r.id} data-testid="goal-ruled-out-aggregate">
                <b>{r.label}</b> — {r.reason}
              </p>
            ))}
          </details>
        </section>
      ) : null}

      <section className="ga-doc-section ga-doc-limits" data-testid="goal-limits">
        <h2 className="ga-doc-h2">What this cannot tell you</h2>
        <p className="ga-doc-lede">
          This reading is qualitative. It sizes a theme by reach — how many
          accounts it touches — and produces no point estimate, effort figure or
          significance test, because nothing it read carries the numbers those
          need.
        </p>
        {/* WHICH FINDINGS APPEAR WAS NOT ALWAYS DECIDED BY THE GOAL, and this
            note is what said so — correctly, until a relevance gate shipped.
            A run that ran the gate must not deny having filtered by it. */}
        {relevanceGateRan ? (
          <p className="ga-doc-note" data-testid="goal-not-selected">
            <strong>These findings were filtered for relevance to your
            goal.</strong>{" "}
            A model checked every theme against your goal and definition and
            kept what could plausibly bear on it; what did not is listed
            separately, with the reason. Being in the evidence you approved AND
            surviving that check is still not a claim about how much a theme
            matters — judge that yourself.
          </p>
        ) : (
          <p className="ga-doc-note" data-testid="goal-not-selected">
            <strong>These findings were not selected for your goal.</strong>{" "}
            Nothing here was filtered or ranked by relevance to your
            definition — a theme appears because it is in the evidence you
            approved, not because it bears on what you asked about. Its
            presence is not a claim that it matters to this goal; judge that
            yourself.
          </p>
        )}
        {gaps.length ? (
          <ul className="ga-doc-gaps">
            {gaps.map((g, i) => (
              <li key={i} data-testid="goal-gap">
                <p className="ga-doc-gap-q">{g.question}</p>
                {/* `stop`, not a bare ".": `g.because` carries the framework
                    reason on the framework gap, which ends in its own full
                    stop, so this rendered "…what it only asks for..". */}
                <p className="ga-doc-gap-why">{stop(`Not answerable here, because ${g.because}`)}</p>
                <p className="ga-doc-gap-fix">
                  <span className="ga-sources-label">To close it</span>{" "}
                  {g.remedy}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="ga-doc-note">
            This run recorded no list of its own gaps, which does not mean it
            had none — only that it predates the step that states them.
          </p>
        )}
      </section>

    </article>
  )
}

export default GoalAnalysisReport
