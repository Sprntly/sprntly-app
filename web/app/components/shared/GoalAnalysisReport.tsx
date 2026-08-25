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
import type { GoalFinding, GoalRunDetail, GoalRunPlan } from "../../lib/api"

/** How many rejections render expanded. Beyond this the ledger folds, because
 *  a run can drop a hundred candidates and an unfolded hundred buries the
 *  closing section under them. */
const RULED_OUT_OPEN_MAX = 12

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

/** One ranked finding, written out: what it says, how big it is, how much of
 *  it we trust, what it rests on, and what had to be assumed to state it. */
function ReportFinding({
  f, rank, sharedWeakest = false, sharedCap = false,
}: {
  f: GoalFinding
  rank: number
  /** The section already stated this sentence once, because every finding
   *  carries the identical one. See `sharedReason` below. */
  sharedWeakest?: boolean
  sharedCap?: boolean
}) {
  return (
    <li className="ga-doc-finding" data-testid="goal-finding">
      <div className="ga-doc-finding-head">
        <span className="ga-doc-rank" aria-hidden="true">{rank}</span>
        <p className="ga-finding-statement">{f.statement}</p>
      </div>
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
      {/* I8: every assumed parameter is disclosed where the number is read,
          not in a methodology page nobody opens. */}
      {f.assumed_params?.length ? (
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
  const findings = run.findings ?? []
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
  const unsized = findings.filter((f) => f.impact_value == null).length
  const anythingSized = unsized < findings.length
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
            {/* WHAT THIS SENTENCE ACTUALLY GOVERNS. "Everything below is
                measured against that sentence and nothing else" is the exact
                claim the closing section denies — claim selection never sees
                the definition. Leaving it put the falsehood five sections
                above its own correction, in the more prominent position. */}
            <p className="ga-doc-note" data-testid="goal-definition-note">
              This is the sentence the run was given to work from, and it is
              recorded here so a decision can be defended against it. It did not
              decide which findings appear below — nothing here was filtered or
              ranked by it. If it is not what you meant, say so before you rely
              on any of this.
            </p>
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
                  sized, so these are ordered by confidence rather than by size
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

      {/* ── 4. The findings, ranked ──────────────────────────────────────── */}
      {findings.length ? (
        <section className="ga-doc-section">
          <h2 className="ga-doc-h2">
            What the evidence says ({findings.length})
          </h2>
          <p className="ga-doc-lede" data-testid="goal-findings-lede">
            {anythingSized ? (
              unsized ? (
                <>
                  Ranked by reach — how many accounts each theme touches, and{" "}
                  {unsized === 1 ? "one" : unsized} of them could not be sized at
                  all. An unsized theme sorts last without being small: its size
                  is unknown, not zero. An authoritative disagreement is placed
                  above everything that is not one, because two sources that may
                  both speak contradicting each other is worth more than either
                  of them alone.
                </>
              ) : (
                <>
                  Ranked by reach — how many accounts each theme touches. An
                  authoritative disagreement is placed above everything that is
                  not one, because two sources that may both speak contradicting
                  each other is worth more than either of them alone.
                </>
              )
            ) : (
              <>
                Not ranked by reach: nothing here could be sized, so these are
                ordered by confidence.
                {/* AND WHETHER THAT ORDER CARRIES ANYTHING. `_rank`'s last term
                    is a confidence SCORE, which is never rendered — the reader
                    sees bands. With no outcome evidence anywhere every band
                    comes out the same, so a list that LOOKS ranked gets read as
                    ranked. Position is the most persuasive thing on a page. */}
                {oneBand ? (
                  <>
                    {" "}Every finding here carries the same confidence band, so
                    that order rests on a score this report does not show you —
                    read the position as a place in a list, not as a verdict on
                    which matters more.
                  </>
                ) : null}{" "}
                An authoritative disagreement is still placed above everything
                that is not one, because two sources that may both speak
                contradicting each other is worth more than either of them
                alone.
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
          <ol className="ga-doc-findings">
            {findings.map((f, i) => (
              <ReportFinding
                key={f.id}
                f={f}
                rank={i + 1}
                sharedWeakest={!!sharedWeakest}
                sharedCap={!!sharedCap}
              />
            ))}
          </ol>
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
        {/* WHICH FINDINGS APPEAR IS NOT DECIDED BY THE GOAL, and nothing on
            screen tells the reader that. The definition gate establishes what
            the goal means with some care and then claim selection never sees
            it, so a run about enterprise churn returns export reliability
            alongside anything that does bear on churn, with nothing marking
            which is which. Stated, because the alternative is a panel that
            LOOKS like it answered the question it was asked. */}
        <p className="ga-doc-note" data-testid="goal-not-selected">
          <strong>These findings were not selected for your goal.</strong>{" "}
          Nothing here was filtered or ranked by relevance to your definition —
          a theme appears because it is in the evidence you approved, not
          because it bears on what you asked about. Its presence is not a claim
          that it matters to this goal; judge that yourself.
        </p>
        {gaps.length ? (
          <ul className="ga-doc-gaps">
            {gaps.map((g, i) => (
              <li key={i} data-testid="goal-gap">
                <p className="ga-doc-gap-q">{g.question}</p>
                <p className="ga-doc-gap-why">Not answerable here, because {g.because}.</p>
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
