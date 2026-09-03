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
  CALL_COUNT_FLOOR_NOTE, MAX_MOSCOW_ROWS, hasCallCount, moscowFor,
  typeBucket,
} from "../../lib/goalMoscow"
import {
  DATA_GAPS_HEADING, ONE_TOPIC_NOTE, dataGapsFor, optionHeader,
  optionNumbers, optionsAreOneTopic,
} from "../../lib/goalDataGaps"
import { stop, stripClaimRefs, upperFirst } from "../../lib/goalProse"
import { frameworkDisplayName } from "../../lib/goalFrameworkDisplay"
import { findingsHeading } from "../../lib/goalFindingsHeading"
import type { GoalFinding, GoalRunDetail, GoalRunPlan } from "../../lib/api"

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

/** One ranked finding, written out: what it says, how big it is, how much of
 *  it we trust, what it rests on, and what had to be assumed to state it. */
function ReportFinding({
  f, rank, sharedWeakest = false, sharedCap = false,
  sharedAssumptions = false, option = 0, dataGaps = [],
  oneTopic = false, oneTopicNote = "", optionTotal = 0,
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
   *  Suppresses "Why this over the next" — there is no "next" being offered as
   *  an alternative, and a sentence reaching for a distinction that does not
   *  exist is worse than silence. */
  oneTopic?: boolean
  /** How many deep write-ups this run rendered — decides whether a single one
   *  is headed as "the" recommendation or as "Option 1" of several. */
  optionTotal?: number
  /** Rendered once, on the recommended card, in place of that comparison. */
  oneTopicNote?: string
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
  return (
    <li className="ga-doc-finding" data-testid="goal-finding">
      {/* THE THEME IS THE HEADING. It used to be the whole sentence — "30
          claims across 11 accounts concern “Sales Pipeline” — for example, …"
          — so the one word a reader scans for sat mid-clause, in quotes,
          behind two numbers the chips on the next line repeat verbatim.
          Heading, chips, quote: each fact once, where it is looked for.
          FALLS BACK TO THE SENTENCE for runs stored before `label` shipped. An
          empty heading would be a worse regression than the run-on. */}
      <div className="ga-doc-finding-head">
        <span className="ga-doc-rank" aria-hidden="true">{rank}</span>
        <p className="ga-finding-statement">
          {(f.label || "").trim() || f.statement}
        </p>
      </div>
      {/* ── WHAT TO DO, FIRST. ────────────────────────────────────────────
          Apurva: "this is only the issues, no suggestion on how to solve or
          what's the exact recommendation from it". The suggestion leads and
          its justification sits under it, so a reader who stops after two
          lines has the actionable half.
          ABSENT IS NORMAL: only the top findings get one, and anything that
          quoted a figure, promised an outcome or failed the lint was dropped
          rather than repaired.
          THE DEEP PASS TAKES PRECEDENCE over the flat one when both exist —
          the same findings feed both LLM calls, and showing both would put
          two suggestions on one finding. */}
      {(f.deep_recommendation?.action || "").trim()
        && (f.deep_recommendation?.because || "").trim() ? (
        <div className="ga-finding-rec" data-testid="goal-finding-recommendation">
          {/* A DIFFERENT HEADER FROM THE FLAT PASS BELOW, deliberately. Both
              used to say the identical "Recommended." — the only visible
              discriminator was whether a "What to change" list happened to
              follow. Mirrors `report.py`'s `_finding_block` fix. */}
          {/* OPTION N, NOT A REPEATED "Recommended". Every deep write-up
              carried the identical header, so a column of them read as a list
              to work through rather than as a choice between named
              alternatives with a stated preference. Option 1 is the one the
              single recommendation is bound to and carries the "Why this over
              the next" sentence below. Mirrors `report.py`'s
              `_finding_block`. */}
          <p><strong>{optionHeader(option, optionTotal, oneTopic)}</strong>{" "}
            {stripClaimRefs(f.deep_recommendation!.action)}</p>
          <p className="ga-finding-rec-why">
            <em>Why.</em> {stripClaimRefs(f.deep_recommendation!.because)}
          </p>
          {f.deep_recommendation!.changes.length ? (
            <>
              <p><strong>What to change.</strong></p>
              <ul className="ga-assumed" data-testid="goal-finding-changes">
                {f.deep_recommendation!.changes.map((c, i) => (
                  <li key={i}>
                    {stripClaimRefs(c.text)} <em>— from: “{c.cited_claim}”</em>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {/* SUPPRESSED ON THE CARD THAT CARRIES THE GAPS LIST — these same
              questions are the middle of it. Shown in both places a reader
              sees one list of open questions and then a second containing
              them again under a heading implying they are something else. */}
          {f.deep_recommendation!.open_questions.length && !dataGaps.length ? (
            <>
              <p><strong>Still open.</strong></p>
              <ul className="ga-assumed">
                {f.deep_recommendation!.open_questions.map((q, i) => (
                  <li key={i}>{q}</li>
                ))}
              </ul>
            </>
          ) : null}
          {f.deep_recommendation!.what_would_falsify ? (
            <p className="ga-weakest" data-testid="goal-finding-kill-signal">
              <b>Kill signal.</b> {stripClaimRefs(f.deep_recommendation!.what_would_falsify)}{" "}
              <em>{KILL_SIGNAL_CAVEAT}</em>
            </p>
          ) : null}
          {/* ALWAYS RENDERED WHEN IT EXISTS. This paragraph is the
              deliverable — "once we pick the top two, then we could just
              compare them" — and an earlier pass suppressed it on exactly the
              runs it was written for: the one-topic branch took it down along
              with the option labels, so the engine computed the sentence and
              the page never printed it. Whether two write-ups are
              alternatives, and which comes first, are different questions.
              The one-topic note sits BESIDE it, never instead of it. */}
          {f.deep_recommendation!.comparison ? (
            <p className="ga-weakest" data-testid="goal-finding-comparison">
              <b>Why this over the next.</b> {f.deep_recommendation!.comparison}
            </p>
          ) : null}
          {oneTopicNote ? (
            <p className="ga-weakest" data-testid="goal-finding-one-topic">
              <b>Why these are not two options.</b> {oneTopicNote}
            </p>
          ) : null}
          {/* ── WHAT WE DO NOT KNOW ABOUT WHAT WE JUST RECOMMENDED. ────────
              Only on the recommended card, assembled deterministically from
              fields the engine already produced — no model call, nothing
              scored (I2). GAPS, NOT ACTIONS: the heading says "close these
              before you spend" rather than "next steps" so it cannot be read
              as work competing with the recommendation above it. Corpus-level
              gaps (`plan.cannot_answer`) are excluded on purpose and rendered
              once, in their own section. Mirrors `report.py`. */}
          {dataGaps.length ? (
            <div data-testid="goal-finding-data-gaps">
              <p>
                <strong>{DATA_GAPS_HEADING}</strong>{" "}
                These are gaps in what is known about this option, not work to
                schedule.
              </p>
              <ul className="ga-assumed">
                {dataGaps.map((g, i) => (
                  <li key={i}>{g}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : (f.recommendation?.action || "").trim()
        && (f.recommendation?.because || "").trim() ? (
        <div className="ga-finding-rec" data-testid="goal-finding-recommendation">
          {/* NOT "Recommended.", WHICH IS THE DEEP CARD'S WORD. Both passes
              used it, so a page with two write-ups and five short-form cards
              said "Recommended" seven times. Mirrors `report.py`. */}
          <p><strong>Suggested.</strong> {stripClaimRefs(f.recommendation!.action)}</p>
          <p className="ga-finding-rec-why">
            <em>Why.</em> {stripClaimRefs(f.recommendation!.because)}
          </p>
          {/* THE SHORTFALL, CONNECTED TO THE FINDING IT ACTUALLY DROPPED —
              not left as a bare fact in "How many got a full recommendation"
              while this card sits below it looking like an unexplained
              absence. `deep_attempted` is only set on a finding that was IN
              the top N a count named or defaulted to but whose evidence did
              not clear the citation gate (or a deep pass that failed
              outright) — never on one simply ranked past N. Mirrors
              `report.py`'s `_finding_block`; the specific reason lives once,
              in the recommendation-basis note above, and this points there
              rather than restating it. */}
          {f.deep_attempted ? (
            <p className="ga-weakest" data-testid="goal-finding-deep-shortfall">
              This finding was one of the ones in line for a full write-up.
              It did not get one this run — see “How many got a full
              recommendation” above for why — so the recommendation above is
              the plain version, not a downgrade of a deeper one you are
              missing.
            </p>
          ) : null}
        </div>
      ) : null}
      <div className="ga-finding-meta">
        <Sized f={f} />
        {f.confidence_band ? (
          <span className="ga-band">{f.confidence_band} confidence</span>
        ) : null}
        {f.adjudication === "conflict" ? (
          <span
            className="ga-conflict"
            title="Two sources that may both speak to this disagree"
          >
            sources disagree
          </span>
        ) : null}
        {f.claim_ids?.length ? (
          <span className="ga-doc-claims">
            {f.claim_ids.length} claim{f.claim_ids.length === 1 ? "" : "s"}
          </span>
        ) : null}
      </div>
      {/* ONE CLAIM, IN ITS SOURCE'S OWN WORDS, set as a quote. Only when the
          heading is the label — with the sentence as the heading the quote is
          already inside it, and repeating it is the duplication this pass
          removes. */}
      {(f.label || "").trim() && (f.example || "").trim() ? (
        <blockquote className="ga-finding-example" data-testid="goal-finding-example">
          “{f.example}”
        </blockquote>
      ) : null}
      {/* The weakest leg is the actionable half of a confidence score: it says
          what to go and find out, which a band on its own never does. */}
      {f.confidence?.weakest_leg_reason && !sharedWeakest ? (
        <p className="ga-weakest">
          <b>Weakest link.</b> {f.confidence.weakest_leg_reason}
        </p>
      ) : null}
      {f.confidence?.cap_reason && !sharedCap ? (
        <p className="ga-cap">{f.confidence.cap_reason}</p>
      ) : null}
      {/* WHERE IT CAME FROM, beside the claim it supports. Without this the
          panel showed the literal word "corpus" as the only provenance, so a
          reader could not check a single finding against anything. */}
      {f.surfaced_by?.length ? (
        <p className="ga-sources" data-testid="goal-sources">
          <span className="ga-sources-label">Source documents</span>{" "}
          {f.surfaced_by.join(" · ")}
        </p>
      ) : null}
      {/* THE FLOOR, SAID IN WORDS. A call provider is extracted one pass per
          call, so a collapsed entry carries how many CALLS it stands for —
          but anything ingested before that changed was batched several calls
          to a document, so the number can only be a lower bound. Printed only
          where a call count is actually shown; "≥" alone is a symbol a reader
          has to interpret and the reason for it is not guessable. Mirrors
          `report.py`'s source block. */}
      {hasCallCount(f.surfaced_by ?? []) ? (
        <p className="ga-cap" data-testid="goal-call-count-floor">
          <em>{CALL_COUNT_FLOOR_NOTE}</em>
        </p>
      ) : null}
      {/* I8: every assumed parameter is disclosed where the number is read,
          not in a methodology page nobody opens. */}
      {f.assumed_params?.length && !sharedAssumptions ? (
        <ul className="ga-assumed">
          {f.assumed_params.map((p) => (
            <li key={p.name}>
              <b>{p.name}</b>: {p.basis}
            </li>
          ))}
        </ul>
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
  // AC-2: how many findings got a full recommendation, and why — a sentence
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

      {/* ── 1. What this was asked to establish ──────────────────────────── */}
      <section className="ga-doc-section" data-testid="goal-definition">
        <h2 className="ga-doc-h2">What this was asked to establish</h2>
        {definition ? (
          <>
            <p className="ga-doc-lede">
              You confirmed this goal means, in your own words:
            </p>
            <blockquote className="ga-doc-quote">{definition}</blockquote>
            {/* WHAT THIS SENTENCE ACTUALLY GOVERNS. Claim SELECTION still
                never sees the definition (`build_findings` runs with no goal
                argument). But the LIST a reader is shown is a different
                question, and `judge_relevance` now answers it, handed this
                exact sentence — so a run that ran the gate must not deny
                having filtered by it. Mirrors `report.py`'s
                `_definition_section`. */}
            {relevanceGateRan ? (
              <p className="ga-doc-note" data-testid="goal-definition-note">
                This is the sentence the run was given to work from, and it is
                recorded here so a decision can be defended against it. It
                shaped which findings appear below: each was checked against
                it for whether it bears on this goal, and any that did not are
                listed separately, with the reason, further down. If it is not
                what you meant, say so before you rely on any of this.
              </p>
            ) : (
              <p className="ga-doc-note" data-testid="goal-definition-note">
                This is the sentence the run was given to work from, and it is
                recorded here so a decision can be defended against it. It did
                not decide which findings appear below — nothing here was
                filtered or ranked by it. If it is not what you meant, say so
                before you rely on any of this.
              </p>
            )}
          </>
        ) : (
          // Stated, not skipped. A report with no recorded definition is a
          // report whose subject is unknown, and hiding that would make it
          // look like the ordinary case.
          <p className="ga-doc-note" data-testid="goal-no-definition">
            No confirmed definition was recorded for this run, so what the goal
            means is not on the record. Read everything below as being about
            the goal as typed, nothing narrower.
          </p>
        )}
      </section>

      {/* ── 2. What was read ─────────────────────────────────────────────── */}
      <section className="ga-doc-section" data-testid="goal-what-was-read">
        <h2 className="ga-doc-h2">What was read</h2>
        {plan ? (
          <>
            <p className="ga-doc-lede">
              {plan.total_signals.toLocaleString()} signal
              {plan.total_signals === 1 ? "" : "s"} across{" "}
              {plan.sources.length} source
              {plan.sources.length === 1 ? "" : "s"}. Each one can witness some
              things and not others, which is why they are listed separately
              rather than totalled.
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
                You excluded {excluded.map(humanSource).join(", ")} before
                this ran, so nothing below rests on it.
              </p>
            ) : null}
          </>
        ) : (
          <p className="ga-doc-note" data-testid="goal-no-plan">
            This run kept no record of which sources it read, so what is below
            cannot be checked against its own inputs.
          </p>
        )}

        {/* Coverage sits HERE — above the findings — and not in a footer.
            A note that a third of the evidence was undated changes how every
            line beneath it should be read, and a degradation discovered after
            the conclusion has already done its damage. */}
        {notes.length ? (
          <>
            <h3 className="ga-doc-h3">What was missing from it</h3>
            <ul className="ga-coverage" data-testid="goal-coverage">
              {notes.map((n, i) => (
                <li key={i}>
                  <b>{n.reason}</b> — {n.actual}
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </section>

      {/* ── 3. The short version ─────────────────────────────────────────── */}
      {/* ── HOW THIS WAS RANKED. ───────────────────────────────────────
          The skill's output spec: the ranked list is the deliverable, and it
          ships WITH a "how we scored it" table so the ranking is reviewable
          rather than a black box — every input marked real or assumed, and the
          one we cannot fill named rather than filled.
          The table NEVER re-sorts: `_rank` froze the order before any of this
          ran, and a scoring table that reordered would be the prioritisation
          step mutating the ranking (I10). */}
      {framework && findings.length && !isMoscowFramework ? (
        <section className="ga-doc-section" data-testid="goal-rice">
          <h2 className="ga-doc-h2">How this was ranked ({frameworkLabel})</h2>
          {frameworkReason ? <p className="ga-doc-note">{frameworkReason}</p> : null}
          <ul className="ga-doc-note">
            <li><strong>Reach</strong> — how many of your accounts the theme
              touches. Counted, not estimated.</li>
            <li><strong>Impact</strong> — how directly it bears on the metric,
              read from the kind of claim behind it: something blocked outranks
              something asked for, which outranks something described.{" "}
              <em>That ordering is ours, not your data&rsquo;s.</em></li>
            <li><strong>Confidence</strong> — the band the evidence earned.</li>
            <li><strong>Effort</strong> — <em>{EFFORT_ABSENT}</em>. Nothing in
              your connected sources carries a person-month, and inventing one
              would put a number in front of you that no evidence supports.</li>
          </ul>
          <div className="ga-rice-scroll">
            <table className="ga-rice">
              <thead>
                <tr>
                  <th>Theme</th><th>Reach</th><th>Impact</th>
                  <th>Confidence</th><th>Effort</th><th>Score</th><th>Inputs</th>
                </tr>
              </thead>
              <tbody>
                {findings.slice(0, MAX_RICE_ROWS).map((f) => {
                  const r = riceFor(f)
                  return (
                    <tr key={f.id}>
                      <td>{r.label}</td>
                      <td>{r.reach === null ? "—" : `${r.reach} ${r.reachUnit}`}</td>
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
              The {findings.length - MAX_RICE_ROWS} findings below these are
              ranked in the list that follows, but not scored out here — a table
              this long stops being one.
            </p>
          ) : null}
          <p className="ga-doc-note">
            No effort estimate was supplied for any of these, so the score is
            reach × impact × confidence. That is not a gap in the ranking: an
            effort applied equally to every row divides them all by the same
            number and cannot change their order.
          </p>
        </section>
      ) : null}

      {/* ── HOW THIS WAS RANKED, WHEN THE FRAMEWORK IS MOSCOW. ─────────────
          MoSCoW is what this run picked when nothing connected carries a
          number — RICE's Reach and Impact would both come back unmeasured on
          every row rather than ranking anything. Same non-reordering
          discipline as the RICE table above (I10): rows render in the order
          `_rank` already froze. */}
      {framework && findings.length && isMoscowFramework ? (
        <section className="ga-doc-section" data-testid="goal-moscow">
          <h2 className="ga-doc-h2">How this was ranked ({frameworkLabel})</h2>
          {frameworkReason ? <p className="ga-doc-note">{frameworkReason}</p> : null}
          <ul className="ga-doc-note">
            <li><strong>MUST</strong> — a stated blocker: something is
              stopping an account today. <em>Marked <strong>MUST?</strong>{" "}
              when only one source document backs it.</em></li>
            <li><strong>SHOULD / COULD</strong> — a stated preference:
              something an account asked for.</li>
            <li>Graded by how many <strong>independent source
              documents</strong> back each one, not by raw claim count.</li>
          </ul>
          <div className="ga-rice-scroll">
            <table className="ga-rice">
              <thead>
                <tr>
                  <th>Theme</th><th>Bucket</th><th>Why</th><th>Reach</th>
                  <th>Source documents</th>
                </tr>
              </thead>
              <tbody>
                {findings.slice(0, MAX_MOSCOW_ROWS).map((f) => {
                  const r = moscowFor(f)
                  return (
                    <tr key={f.id}>
                      <td>{r.label}</td>
                      <td>{r.bucket}</td>
                      <td>{r.bucketBasis}</td>
                      <td>{r.reach === null ? "—" : `${r.reach} ${r.reachUnit}`}</td>
                      <td>{r.docCount}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {findings.length > MAX_MOSCOW_ROWS ? (
            <p className="ga-doc-note">
              The {findings.length - MAX_MOSCOW_ROWS} findings below these are
              ranked in the list that follows, but not bucketed out here — a
              table this long stops being one.
            </p>
          ) : null}
        </section>
      ) : null}

      {/* ── THE HEADLINE NUMBERS, ON ONE LINE. ────────────────────────────
          Memo p1 closes its cover with a strip. Every number in it already
          exists in this document, spread across four paragraphs — the strip is
          the difference between knowing the shape of the answer at a glance
          and assembling it yourself.
          NO DATA WINDOW cell: claim dates on this substrate are the INGEST
          clock, so a window printed from them would be when we read the
          evidence wearing the clothes of the period it covers. */}
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
              // prose below names ("the top 2 get a full recommendation"),
              // which counts only the DEEP pass. This cell counts flat OR
              // deep — the union, mirroring `report.py`'s `_stat_strip` —
              // so a run can show 8 here and 2 there, both true, about two
              // different senses of the word. Also fixed here: this used to
              // check `f.recommendation` alone, undercounting a finding whose
              // ONLY suggestion was the deep one.
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

      {/* ── THE FUNNEL, BEFORE ANYTHING IS SHOWN. ─────────────────────────
          The first thing a filtered list owes its reader. A filtered list that
          does not say it was filtered is the more confident-looking of the two,
          and the less honest. Silent when nothing was set aside — a funnel with
          one step is not a funnel. */}
      {setAside.length ? (
        <section className="ga-doc-section" data-testid="goal-funnel">
          <h2 className="ga-doc-h2">What bears on this goal</h2>
          <p className="ga-doc-lede">
            <strong>
              {allFindings.length} themes were found. {findings.length} bear on
              this goal.
            </strong>{" "}
            The other {setAside.length} are listed at the end with the reason
            each was set aside — they are not gone, and a theme set aside for
            this goal may be the answer to a different one.
          </p>
        </section>
      ) : null}
      {/* The relevance gate's disclosure half. `relevance.py` promises "the renderer says
          how many were not evaluated" — the gate has a hard budget and a
          wall-clock deadline that can stop it early, and until this fired,
          nothing ever said so. Separate from the funnel above: this fires
          even when nothing was set aside, because a reader still needs to
          know the "found" count can include themes the gate never got to.
          Mirrors `report.py`'s `_relevance_coverage_section`. */}
      {relevanceJudged && relevanceJudged.considered > relevanceJudged.judged ? (
        <section className="ga-doc-section" data-testid="goal-relevance-coverage">
          <p className="ga-doc-note">
            Of the {relevanceJudged.considered} themes found, this run
            evaluated {relevanceJudged.judged} for relevance to your goal
            before its time or cost budget ran out. The other{" "}
            {relevanceJudged.considered - relevanceJudged.judged} were never
            judged and are kept in the list above — unjudged, not irrelevant.
          </p>
        </section>
      ) : null}
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
                  for — with how sure we are breaking ties inside a kind
                  — the order says how sure each one is, not how big.
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

      {/* THE ONE RECOMMENDATION FOR THE WHOLE REPORT — narrated across the
          per-finding deep recommendations already shown above, never a
          replacement for them. Positioned right after "The short version" so
          it reads as the primary answer, ahead of the money footnotes and the
          findings list below. Mirrors `report.py`'s
          `_synthesized_recommendation_section`, same wording and structure.
          Silent when there was nothing to synthesize — see the type's own
          comment in `api.ts`.
          KEPT IN ITS OWN SECTION, never merged into `recommendationBasis`'s
          or `listPricingBasis`'s paragraph below: one is a narrated
          recommendation, the other two are dollar-bearing footnotes, and a
          reader should never be tempted to read one as informing the
          other. */}
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

      {recommendationBasis ? (
        <p className="ga-doc-note" data-testid="goal-recommendation-basis">
          {/* CAPITALISED AND TERMINATED, because it follows a bold full stop
              and is therefore the start of a sentence. `recommendationBasis`
              is authored as a clause — it also renders mid-sentence elsewhere
              — so left alone it produced "How many got a full recommendation.
              you named a target of…". Mirrors `report.py`. */}
          <strong>How many got a full recommendation.</strong>{" "}
          {stop(upperFirst(recommendationBasis))}
        </p>
      ) : null}

      {/* A RANGE, IN ITS OWN PARAGRAPH, NEVER BESIDE THE COMMITTED FIGURE
          ABOVE. One is a sum of money people agreed to; the other is a rate
          card quoted to whoever asked, whose total is meaningless — a
          $30,000 tier quoted sixteen times is not $480,000. Kept as its own
          block, never merged into `recommendationBasis`'s paragraph or any
          other, so a reader is never shown a range and a sum close enough
          together to be tempted to add them. Mirrors `report.py`'s
          `_findings_section`, which places its own list-pricing paragraph
          the same way. */}
      {listPricingBasis ? (
        <p className="ga-doc-note" data-testid="goal-list-pricing-basis">
          {listPricingBasis}
        </p>
      ) : null}

      {/* ── 4. The findings, ranked ──────────────────────────────────────── */}
      {findings.length ? (
        <section className="ga-doc-section">
          {/* A CLAIM, NOT A LABEL. Mirrors `report.py`'s `_findings_heading`
              exactly, via the shared `findingsHeading` helper — see its own
              comment for why this is the top-ranked finding's own statement,
              cut before its example quote, rather than a static section
              title. */}
          <h2 className="ga-doc-h2">{findingsHeading(findings)}</h2>
          <p className="ga-doc-lede" data-testid="goal-findings-lede">
            {anythingSized ? (
              unsized && headlineCovers !== "full" ? (
                <>
                  Ranked by reach — how many accounts each theme touches, and{" "}
                  {unsized === 1 ? "one" : unsized} of them could not be sized at
                  all.
                  {/* The caveat only when the headline did not make it. With an
                      unsized top row it did — three lines above — and saying it
                      again is the repetition the feedback named. */}
                  {headlineCovers === "none" ? (
                    <>
                      {" "}An unsized theme sorts last without being small: its
                      size is unknown, not zero.
                    </>
                  ) : null}{" "}
                  {bucketClause}An authoritative disagreement is placed
                  above both, because two sources that may
                  both speak contradicting each other is worth more than either
                  of them alone.
                </>
              ) : (
                <>
                  Ranked by reach — how many accounts each theme touches.{" "}
                  {bucketClause}An
                  authoritative disagreement is placed above both, because two
                  sources that may both speak contradicting each other is worth
                  more than either of them alone.
                </>
              )
            ) : (
              <>
                {/* WHAT THE ORDER ACTUALLY IS, before any caveat about it.
                    `_rank`'s key is (conflict, claim-type bucket, reach,
                    confidence), and this paragraph used to omit the bucket
                    entirely — so on a corpus nothing could size it said the
                    list was "ordered by confidence" when what had ordered it
                    was blockers above preferences. Mirrors `report.py`. */}
                Not ranked by reach: nothing here could be sized.{" "}
                {bucketClause}An authoritative disagreement is placed above
                both, because two sources that may both speak contradicting
                each other is worth more than either of them alone.
                {/* AND WHETHER WHAT IS LEFT CARRIES ANYTHING. `_rank`'s last
                    term is a confidence SCORE, never rendered — the reader
                    sees bands, and with no outcome evidence anywhere every
                    band comes out the same, so a caveat is owed. But the
                    caveat used to read "not as a verdict on which matters
                    more", printed inches under a recommendation BOUND to
                    position 1 — the page telling a reader to act on a ranking
                    while disowning it. Blockers genuinely do sort above
                    preferences now, so position carries real meaning down to
                    where the bucket runs out. Scoped to what is still true. */}
                {oneBand ? (
                  <>
                    {" "}Past that, findings of the same kind are ordered by a
                    confidence score this report does not print, and every
                    finding here carries the same confidence band — so read the
                    gap between two neighbours in the same group as narrow,
                    rather than as a verdict on which matters more.
                  </>
                ) : null}
              </>
            )}
          </p>
          {sharedWeakest ? (
            <p className="ga-doc-note" data-testid="goal-shared-weakest">
              <strong>Every finding below has the same weakest link</strong>, so
              it is stated here once rather than repeated on each of them:{" "}
              {sharedWeakest}
              {/* The cap is its own sentence and arrives uncapitalised
                  ("capped at medium: …"), so joining it after a full stop
                  rendered "…are not. capped at medium". Joined with a
                  semicolon it reads as the clause it is. */}
              {sharedCap ? `; ${sharedCap}.` : "."}
            </p>
          ) : sharedCap ? (
            <p className="ga-doc-note" data-testid="goal-shared-cap">
              <strong>Every finding below is capped the same way</strong>, so it
              is stated here once rather than on each of them: {sharedCap}.
            </p>
          ) : null}
          {sharedAssumptions?.length ? (
            <div className="ga-doc-note" data-testid="goal-shared-assumptions">
              <p>
                {/* SAYS HOW MANY IT SPEAKS FOR. "Every finding below" is false
                    when only the sized ones carry an assumption, and a hoisted
                    sentence that overstates its scope is worse than the
                    repetition it replaced. */}
                <strong>
                  {carriers.length === findings.length
                    ? "Every finding below rests on the same assumption"
                    : `${carriers.length} of the findings below rest on the same assumption`}
                  {sharedAssumptions.length > 1 ? "s" : ""}
                </strong>
                , so {sharedAssumptions.length > 1 ? "they are" : "it is"}{" "}
                stated here once rather than repeated on each of them:
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
          <ol className="ga-doc-findings">
            {findings.map((f, i) => (
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
              />
            ))}
          </ol>
          {/* ── CONSIDERED AND SET ASIDE. ───────────────────────────────────
              NOT A DELETION. Each of these was found, corroborated and ranked
              exactly like the findings above; what changed is that it does not
              answer the question that was asked. The reason beside each is what
              makes the filter arguable — a reader who disagrees can see precisely
              what was judged and say so. */}
          {setAside.length ? (
            <div className="ga-doc-aside" data-testid="goal-set-aside">
              <h3 className="ga-doc-h3">
                Considered and set aside for this goal ({setAside.length})
              </h3>
              <p className="ga-doc-note">
                Each of these was found and ranked like the findings above. They
                are here because they do not bear on the goal as you defined it,
                not because the evidence was weak.
              </p>
              {/* THE MEMO'S FOUR COLUMNS. A bullet list carried two of them — the
                  label and the reason — so what the theme actually SAID and what it
                  was worth were dropped, which are the two a reader needs in order to
                  disagree with the verdict. */}
              <div className="ga-rice-scroll">
                <table className="ga-rice">
                  <thead>
                    <tr>
                      <th>Theme</th><th>What it is</th>
                      <th>Worth this cycle</th><th>Why it was set aside</th>
                    </tr>
                  </thead>
                  <tbody>
                    {setAside.map(([f, reason]) => (
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
            </div>
          ) : null}
        </section>
      ) : null}

      {/* ── 5. What you already believed ─────────────────────────────────── */}
      {hypotheses.length ? (
        <section className="ga-doc-section" data-testid="goal-hypotheses">
          <h2 className="ga-doc-h2">What you already believed</h2>
          <ul className="ga-doc-list">
            {hypotheses.map((h, i) => (
              <li key={i}>{h}</li>
            ))}
          </ul>
          {/* NOT a verdict. The engine does not yet test a stated hypothesis
              against the claims, and rendering these beside the findings
              without saying so would let a reader infer that silence meant
              "not supported" — which would be a conclusion nothing produced. */}
          <p className="ga-doc-note">
            This reading did not test these. It reports what it found, and
            nothing above was matched against what you wrote here — so their
            absence from the findings is not evidence against them.
          </p>
        </section>
      ) : null}

      {/* ── 6. Considered and ruled out ──────────────────────────────────── */}
      {run.considered?.length ? (
        <section className="ga-doc-section" data-testid="goal-considered">
          {/* OPEN while the list is short, folded once it is long — but the
              COUNT is in the summary either way, so the ledger is never
              silently thin. A run can reject a hundred candidates, and an
              unfolded hundred pushes "what this cannot tell you" off the end
              of the document, which is the one section a reader must reach. */}
          <details open={run.considered.length <= RULED_OUT_OPEN_MAX}>
            <summary className="ga-doc-h2 ga-doc-summary">
              Considered and ruled out ({ruledOutCandidates.length})
            </summary>
            {/* WHAT KILLED THEM, not just that something did. "Each one died
                for a stated reason" is true and, printed beside every label,
                reads as 102 reasons — a real run had 102 rejections and FIVE
                causes, half of them one rule and half another, which the flat
                list made invisible without counting by hand. The counts are
                the actionable part: one cause at 49 is one thing to fix. */}
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

      {/* ── 7. What this cannot tell you ─────────────────────────────────── */}
      <section className="ga-doc-section ga-doc-limits" data-testid="goal-limits">
        <h2 className="ga-doc-h2">What this cannot tell you</h2>
        <p className="ga-doc-lede">
          This reading is qualitative. It sizes a theme by reach — how many
          accounts it touches — and it does not produce a point estimate, an
          effort figure, a prioritisation score or a significance test, because
          nothing it read carries the numbers those need. Where you expected one
          of those, this is why it is absent.
        </p>
        {/* WHICH FINDINGS APPEAR WAS NOT ALWAYS DECIDED BY THE GOAL, and this
            note is what said so — correctly, until a relevance gate shipped.
            Claim SELECTION still never sees the goal, but the list a reader
            is SHOWN is a different question, and `judge_relevance` now
            answers it. A run that ran the gate must not deny having filtered
            by it. `relevanceGateRan` is true only when the gate
            completed without raising. Mirrors `report.py`'s
            `_limits_section`. */}
        {relevanceGateRan ? (
          <p className="ga-doc-note" data-testid="goal-not-selected">
            <strong>These findings were filtered for relevance to your
            goal.</strong>{" "}
            A model checked every theme against your goal and definition and
            kept what could plausibly bear on it; what did not is listed
            separately below, with the reason. Being in the evidence you
            approved AND surviving that check is still not a claim about how
            much a theme matters — judge that yourself.
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
