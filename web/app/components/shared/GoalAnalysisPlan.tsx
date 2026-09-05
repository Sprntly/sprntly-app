"use client"

/**
 * The SECOND gate: the run says what it will do, before it does it.
 * (Engine name Crucible; that word never appears on screen.)
 *
 * WHY THIS SCREEN EXISTS. A run reads a company's whole knowledge graph and
 * takes minutes. Until this step, the first thing a user learned about its
 * limits was the coverage notes at the BOTTOM of the finished output — after
 * the wait, and phrased as an apology. The same facts beforehand are a
 * decision instead: connect the missing source, drop one that would only add
 * noise, or accept a qualitative answer knowingly.
 *
 * IT IS A METHOD NOW, NOT AN INVENTORY. The server composes a numbered
 * sequence of operations it can actually perform and sends it as `plan.steps`;
 * this card renders it. What changed on screen follows from that:
 *
 *  - THE STEPS ARE THE CENTRE. Numbered continuously across their parts, one
 *    short action phrase each. The `why` stacks UNDERNEATH and is hidden
 *    behind one control, because inline after a dash defeats scanning — the
 *    eye has to read every line to find the next action, so twenty-three steps
 *    read as a wall. Collapsed it is a column of actions; expanded it is the
 *    full method. Both present, one control apart.
 *
 *  - NOTHING ON THE READING SURFACE IS A FORM FIELD. The old card put an open
 *    textarea mid-page for the definition and three inputs at the bottom, so a
 *    reader was asked to fill things in while still trying to read. The
 *    definition is a quoted statement, the sources are ticks, and the two
 *    things that genuinely remain editable sit one quiet disclosure away.
 *
 *  - THE GATE IS TWO STEPS. Approve the plan; it collapses to a record of what
 *    was approved, and the questions appear on their own. The questions depend
 *    on the approved plan, so they follow it — asked alongside it they read as
 *    optional decoration, which is exactly the "none of this is really
 *    important" the old single screen produced.
 *
 * ONE POST, TWO STAGES. `/approve` is the only endpoint and it starts the run,
 * so the answers have to travel WITH the approval. Stage one is therefore a
 * client-side commit, not a request: the first button records the reader's
 * agreement and reveals what is still needed, and the second is the one that
 * actually starts anything. The labels say which is which, because a button
 * that reads "approve and run" and does not run is worse than either.
 *
 * WHAT IS DELIBERATELY ABSENT: a "change the approach" box. There is no
 * re-plan endpoint, and a box that swallows a sentence and changes nothing is
 * worse than no box.
 */
import * as React from "react"
import { useMemo, useState } from "react"
import { planNarrative } from "../../lib/goalPlanNarrative"
import type { GoalPlanQuestion, GoalPlanStep, GoalRunPlan } from "../../lib/api"
import s from "./GoalAnalysisPlan.module.css"

/** The three questions asked before `plan.questions` existed, used only as a
 *  fallback for a plan stored before this field landed — a run still sitting
 *  `awaiting_approval` from before this shipped must not lose its "what I
 *  cannot know" section outright. Every NEW plan carries its own derived list;
 *  this is not the default going forward. */
const LEGACY_QUESTIONS: GoalPlanQuestion[] = [
  { id: "account_value", prompt: "What is one account worth to you, per year?", why: "" },
  { id: "decision_owner", prompt: "Who decides this?", why: "" },
  { id: "needed_by", prompt: "When do you need the decision?", why: "" },
]

/** The questions whose answers never touch the analysis.
 *
 *  Mirrors the server's own decision-box pair. They are separated on screen
 *  because "who decides this" and "which of these two columns is your book"
 *  are not the same kind of question: one annotates the last page of the
 *  report, the other rescales every number in it. Sitting them in one list
 *  told a reader they were equally consequential, and the reader correctly
 *  concluded that none of them were. */
const REPORT_ONLY_QUESTION_IDS = new Set(["decision_owner", "needed_by"])

/** Question ids with a dedicated field on `/approve`. Anything else is a
 *  DERIVED question and travels in the generic `answers` map. */
const WIRED_QUESTION_IDS = new Set([
  "account_value", "decision_owner", "needed_by",
])

export type PlanDecision = {
  excluded_sources: string[]
  hypotheses: string[]
  /** The definition this click adopts, sent ONLY when the reader edited the
   *  proposal. Absent means "as shown", which the server reads off the plan it
   *  stored — so an untouched approve cannot round-trip the definition through
   *  the client, where a stale card could overwrite it with old words. */
  definition_text?: string
  /** ── ANSWERS TO WHAT THE RUN CANNOT KNOW. ────────────────────────────
   *  All optional. Skipping them yields exactly the document you got before,
   *  with the affected sections stating what is missing rather than guessing.
   *  A value given here is an ASSUMPTION, not evidence, and the document says
   *  so where it uses it.
   *
   *  EVERY ONE OF THESE STAYS OPTIONAL, and that is a constraint rather than
   *  a preference: the approve path is asserted against exact objects, so an
   *  always-present field would change every call this component makes. */
  account_value?: number
  decision_owner?: string
  needed_by?: string
  /** Answers to the DERIVED questions, keyed by question id. */
  answers?: Record<string, string>
}

/** Mirrors the API's per-hypothesis cap. One place it can drift, stated here
 *  rather than discovered as a 422. */
export const MAX_HYPOTHESIS_CHARS = 2_000

type Stage = "plan" | "questions"

/** A step as this card renders it.
 *
 *  `items` is CLIENT-SIDE ONLY and never arrives from the server: the composed
 *  method has one action phrase per step, and only the legacy narrative
 *  fallback splits a step into sub-points. It is typed here rather than on
 *  `GoalPlanStep` so the wire type keeps describing the wire. */
type RenderStep = GoalPlanStep & { items?: string[] }

export function GoalAnalysisPlan({
  plan,
  approving,
  onApprove,
  settled,
}: {
  plan: GoalRunPlan
  approving: boolean
  onApprove: (decision: PlanDecision) => void
  /** THE PLAN STAYS IN THE THREAD after it is approved, read-only.
   *
   *  It used to collapse into a four-line receipt — "Plan approved", the source
   *  counts, and nothing else. That threw away the entire thing a PM has to be
   *  able to point at later: which sources were in scope, what each one can
   *  actually witness, what the run said up front it would NOT be able to
   *  answer. Scrolling back showed that a plan was approved, not what was
   *  approved, which is the whole reason the gate is in the conversation and
   *  not in a modal.
   *
   *  Same component, so the record cannot drift from the thing that was agreed
   *  to — two renderers of one plan is a mistake this card has had to come
   *  back for once already. */
  settled?: { excludedSources: string[]; hypotheses: string[] }
}) {
  // Excluded, not included: the default is "read everything", so an empty set
  // is the untouched state and no source can be dropped by an off-by-one.
  const [excluded, setExcluded] = useState<Set<string>>(new Set())
  const [hypothesesText, setHypothesesText] = useState("")
  // THE DEFINITION, EDITABLE IN PLACE. `null` is untouched — distinct from a
  // string equal to the proposal, because only `null` may be omitted from the
  // approve body. A reader who selects the text, retypes it identically and
  // approves has still adopted it; they just have not changed it.
  const [definitionEdit, setDefinitionEdit] = useState<string | null>(null)
  // ONE MAP FOR EVERY ANSWER, keyed by question id. Three separate pieces of
  // state worked while the question set was fixed at three; it cannot survive
  // a set derived from what the evidence happened to contain.
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [stage, setStage] = useState<Stage>("plan")
  const [showHow, setShowHow] = useState(false)
  const [rewording, setRewording] = useState(false)
  const [changingSources, setChangingSources] = useState(false)

  // A settled record reads its exclusions from what was actually posted, not
  // from local state a re-mount would have thrown away.
  const effectiveExcluded = settled ? new Set(settled.excludedSources) : excluded
  const kept = useMemo(
    () => plan.sources.filter((x) => !effectiveExcluded.has(x.source_type)),
    [plan.sources, effectiveExcluded],
  )
  const keptSignals = kept.reduce((n, x) => n + x.signal_count, 0)
  const nothingLeft = plan.sources.length > 0 && kept.length === 0
  // ONE RESOLUTION OF THE STEP LIST, shared by the plan body and the
  // collapsed record. Resolving it twice would let the record disagree with
  // the document about how many steps were approved.
  const steps = useMemo<RenderStep[]>(
    () => planSteps(plan, effectiveExcluded), [plan, effectiveExcluded],
  )

  const toggle = (sourceType: string) => {
    setExcluded((prev) => {
      const next = new Set(prev)
      if (next.has(sourceType)) next.delete(sourceType)
      else next.add(sourceType)
      return next
    })
  }

  // Mirrors `HYPOTHESIS_MAX_CHARS` on `/v1/crucible/{id}/approve`. The API
  // REJECTS an over-long line rather than truncating it, and a 422 there is
  // unrecoverable from the panel: the run stays `awaiting_approval`, so the
  // user retries the same text forever. Caught here, where the offending line
  // can actually be named.
  const tooLong = hypothesesText
    .split("\n")
    .map((h) => h.trim())
    .filter((h) => h.length > MAX_HYPOTHESIS_CHARS)

  // ONLY WHAT THIS RUN NEEDS. A plan stored before `questions` existed falls
  // back to the old fixed three rather than losing the section outright.
  const questions = plan.questions?.length ? plan.questions : LEGACY_QUESTIONS
  const methodQuestions = questions.filter((q) => !REPORT_ONLY_QUESTION_IDS.has(q.id))
  const reportQuestions = questions.filter((q) => REPORT_ONLY_QUESTION_IDS.has(q.id))
  // A DERIVED question is one with no dedicated field on the approve body.
  const derivedAsked = questions.some((q) => !WIRED_QUESTION_IDS.has(q.id))

  const submit = () => {
    if (approving || nothingLeft || tooLong.length) return
    const editedDefinition =
      definitionEdit !== null && definitionEdit.trim() &&
      definitionEdit.trim() !== (plan.definition_text || "").trim()
        ? definitionEdit.trim()
        : undefined
    // EMPTY MEANS UNANSWERED, never zero. A blank account value must not
    // reach the arithmetic as 0 — that would render a stake of nothing and
    // read as a measurement.
    const value = Number.parseFloat(
      (answers.account_value || "").replace(/[^0-9.]/g, ""),
    )
    const owner = (answers.decision_owner || "").trim()
    const needed = (answers.needed_by || "").trim()
    // Everything without a dedicated field travels under `answers`. Built by
    // EXCLUSION rather than from a list of known derived ids, so a question
    // the server adds tomorrow reaches the wire without a client release.
    const derived: Record<string, string> = {}
    for (const [id, v] of Object.entries(answers)) {
      if (WIRED_QUESTION_IDS.has(id)) continue
      if (v && v.trim()) derived[id] = v.trim()
    }
    onApprove({
      ...(Number.isFinite(value) && value > 0 ? { account_value: value } : {}),
      ...(owner ? { decision_owner: owner } : {}),
      ...(needed ? { needed_by: needed } : {}),
      ...(editedDefinition ? { definition_text: editedDefinition } : {}),
      ...(Object.keys(derived).length ? { answers: derived } : {}),
      excluded_sources: [...excluded],
      // One per line. Blank lines are dropped rather than sent as empty
      // hypotheses, which would be counted and reported back as things the
      // user believed.
      hypotheses: hypothesesText
        .split("\n")
        .map((h) => h.trim())
        .filter(Boolean),
    })
  }

  const showingQuestions = !settled && stage === "questions"
  const showingPlan = settled || stage === "plan"

  return (
    <div className="ga-plan" data-testid="goal-plan">
      <header className="ga-doc-header">
        <p className="ga-doc-eyebrow">
          {settled
            ? "Plan approved"
            : showingQuestions
              ? "Before this runs"
              : "Before this runs"}
        </p>
        <h1 className="ga-doc-title">{plan.goal_text}</h1>
        {/* THE READER'S OWN WORDS, beside what they were taken to mean.
            Only when there is one to show and it actually differs — a run
            with no literal text (the direct API, the `+` menu) shows just
            the goal, exactly as before this existed. */}
        {plan.asked_text && plan.asked_text.trim() !== plan.goal_text.trim() ? (
          <p className="ga-doc-note" data-testid="goal-plan-asked-text">
            You asked: “{plan.asked_text}”
          </p>
        ) : null}
      </header>

      {/* ── THE APPROVED PLAN, COLLAPSED. ───────────────────────────────
          Stage two needs its subject on screen without the whole document
          above it: the questions are about THIS plan, and a reader who has to
          scroll past twenty-three steps to reach them has been given a second
          document, not a second step. */}
      {showingQuestions ? (
        <div className={s.record} data-testid="goal-plan-record">
          <p className={s.recordNote}>
            Approved: {steps.length} step{steps.length === 1 ? "" : "s"} over{" "}
            {kept.length} source{kept.length === 1 ? "" : "s"}
            {plan.currency ? `, sized in ${plan.currency}` : ""}.
          </p>
          <button
            type="button"
            className={s.reopen}
            onClick={() => setStage("plan")}
          >
            Back to the plan
          </button>
        </div>
      ) : null}

      {showingPlan ? (
        <PlanBody
          plan={plan}
          steps={steps}
          settled={settled}
          kept={kept}
          keptSignals={keptSignals}
          effectiveExcluded={effectiveExcluded}
          showHow={showHow}
          setShowHow={setShowHow}
          rewording={rewording}
          setRewording={setRewording}
          definitionEdit={definitionEdit}
          setDefinitionEdit={setDefinitionEdit}
          changingSources={changingSources}
          setChangingSources={setChangingSources}
          toggle={toggle}
          questionCount={questions.length}
        />
      ) : null}

      {/* ── WHAT I STILL NEED FROM YOU. ─────────────────────────────────
          On its own, after the plan is agreed. Apurva: "the plan gate can
          start asking questions it doesn't know answers to." Asked alongside
          the plan these read as optional decoration; asked after it they are
          the one thing left to do. */}
      {showingQuestions ? (
        <>
          <section className="ga-plan-section" data-testid="goal-plan-unknowns">
            <h2 className="ga-doc-h3">What I cannot work out myself</h2>
            <p className="ga-doc-note">
              Answer what you can — anything you leave blank stays stated as
              missing rather than filled in.
            </p>

            {methodQuestions.length ? (
              <>
                <p className={s.statLabel}>These change the analysis</p>
                {/* THE HONESTY THE ANSWER REQUIRES. No pass reads these yet:
                    the run carries out the default each question states, and
                    the answer is stored on the plan for the pass that will
                    honour it. Saying so here is the difference between a
                    record and a control that lies. */}
                {derivedAsked ? (
                  <p className="ga-doc-note" data-testid="goal-plan-derived-note">
                    Recorded with the plan. This run still does what each
                    question says it will do by default — these are on the
                    record for the next one.
                  </p>
                ) : null}
                <div className={s.questions}>
                  {methodQuestions.map((q) => (
                    <QuestionCard
                      key={q.id}
                      question={q}
                      value={answers[q.id] ?? ""}
                      onChange={(v) =>
                        setAnswers((prev) => ({ ...prev, [q.id]: v }))
                      }
                    />
                  ))}
                </div>
              </>
            ) : null}

            {reportQuestions.length ? (
              <>
                <p className={s.statLabel}>These only annotate the report</p>
                <div className={s.questions}>
                  {reportQuestions.map((q) => (
                    <QuestionCard
                      key={q.id}
                      question={q}
                      value={answers[q.id] ?? ""}
                      onChange={(v) =>
                        setAnswers((prev) => ({ ...prev, [q.id]: v }))
                      }
                    />
                  ))}
                </div>
              </>
            ) : null}
          </section>

          <section className="ga-plan-section">
            <h2 className="ga-doc-h3">What do you already believe?</h2>
            <p className="ga-doc-note">
              Optional, one per line. A run can always tell you what it found;
              told what you expected, it can also record what it did not find.
            </p>
            <textarea
              className="ga-definition"
              aria-label="What you already believe"
              rows={3}
              value={hypothesesText}
              placeholder={"onboarding is where they drop off\npricing is the blocker"}
              onChange={(e) => setHypothesesText(e.target.value)}
            />
          </section>
        </>
      ) : null}

      {settled && settled.hypotheses.length ? (
        <section className="ga-plan-section" data-testid="goal-plan-settled-hypotheses">
          <h2 className="ga-doc-h3">What you already believed</h2>
          <ul className="ga-doc-list">
            {settled.hypotheses.map((h, i) => <li key={i}>{h}</li>)}
          </ul>
        </section>
      ) : null}

      {showingQuestions && tooLong.length ? (
        <p className="ga-doc-note" data-testid="goal-plan-hypothesis-too-long">
          {tooLong.length === 1
            ? `One hypothesis is ${tooLong[0].length} characters long. `
            : `${tooLong.length} of these are over ${MAX_HYPOTHESIS_CHARS} characters. `}
          Each one has to be under {MAX_HYPOTHESIS_CHARS} characters — shorten
          it, or split it across lines.
        </p>
      ) : null}

      {!settled && nothingLeft ? (
        <p className="ga-doc-note" data-testid="goal-plan-empty-warning">
          Every source is unchecked, so there is nothing left to read.
        </p>
      ) : null}

      {/* NO BUTTON ON A SETTLED PLAN. The decision is made; a control that
          cannot act is worse than none, because it reads as one that can. */}
      {settled ? null : stage === "plan" && questions.length ? (
        <button
          type="button"
          className="ga-confirm"
          disabled={nothingLeft}
          onClick={() => setStage("questions")}
        >
          Approve this plan
        </button>
      ) : (
        <button
          type="button"
          className="ga-confirm"
          disabled={approving || nothingLeft}
          onClick={submit}
        >
          {approving ? "Starting…" : "Approve and run"}
        </button>
      )}
    </div>
  )
}

/** The steps this card will render, server-composed or derived.
 *
 *  ONE GENERATOR PER PLAN, NEVER TWO. The server now composes the method, and
 *  `planNarrative` is kept strictly as the fallback for a plan that has no
 *  `steps` — every plan stored before this shipped, including any sitting
 *  `awaiting_approval` right now. It is unreachable whenever `steps` is
 *  present, and nothing new goes into it: two live generators for one list is
 *  exactly the drift that makes a plan describe a run that no longer happens.
 */
function planSteps(
  plan: GoalRunPlan, excluded: ReadonlySet<string>,
): RenderStep[] {
  if (plan.steps?.length) return plan.steps
  // ITEMS STAY A LIST. Joining them into `what` with a space produced exactly
  // the run-on the narrative already learned not to write: "…something an
  // account asked for Graded by how many independent source documents…", four
  // bullets welded into one five-line paragraph with no sentence boundary
  // between them. Two steps rendered that way and the rest as single lines, so
  // the column of actions had no consistent left edge to run down — which is
  // the entire thing the step list is shaped for.
  return planNarrative(plan, excluded).map((step, i) => ({
    n: i + 1,
    part: "",
    primitive: "",
    what: step.text,
    why: "",
    items: step.items,
  }))
}

function PlanBody({
  plan, steps, settled, kept, keptSignals, effectiveExcluded,
  showHow, setShowHow, rewording, setRewording,
  definitionEdit, setDefinitionEdit,
  changingSources, setChangingSources, toggle, questionCount,
}: {
  plan: GoalRunPlan
  steps: RenderStep[]
  settled?: { excludedSources: string[]; hypotheses: string[] }
  kept: GoalRunPlan["sources"]
  keptSignals: number
  effectiveExcluded: ReadonlySet<string>
  showHow: boolean
  setShowHow: (v: boolean) => void
  rewording: boolean
  setRewording: (v: boolean) => void
  definitionEdit: string | null
  setDefinitionEdit: (v: string) => void
  changingSources: boolean
  setChangingSources: (v: boolean) => void
  toggle: (sourceType: string) => void
  questionCount: number
}) {
  const coverage = plan.coverage
  const gaps = plan.cannot_answer ?? []

  // THE VERDICT, IN A SENTENCE, BEFORE ANY NUMBER. A reader arriving at a plan
  // asks whether this can be answered at all; the strip below answers "off how
  // much" and cannot answer the first question.
  const verdict = !plan.sources.length
    ? "Nothing is connected for this to read."
    : gaps.length
      ? `I can answer this from what you have connected, with ${gaps.length} ` +
        `thing${gaps.length === 1 ? "" : "s"} I will not be able to settle.`
      : "I can answer this from what you have connected."

  // THE UNIT LINE IS NOT A COPY OF A STEP. It used to lift its sentence from
  // `set_counting_unit`, which then rendered again in the numbered list about
  // 250px further down — the same words twice on one card, which reads as a
  // template that lost track of itself. The serif line makes the general
  // statement; the step states the action, in the model's own words.

  return (
    <>
      <section className="ga-plan-section" data-testid="goal-plan-verdict">
        <p className={s.verdict}>{verdict}</p>
        {/* NO FALLBACK TOTAL WHEN THERE IS NO COVERAGE SUMMARY. A plan
            without one still names its signal count in the steps, and
            printing it here as well put the same number twice on one card —
            the exact duplication an earlier pass removed from this section,
            reintroduced by giving the section a lede again. The strip renders
            only when there are coverage facts the steps do NOT carry. */}
        {coverage ? (
          <dl className={s.strip} data-testid="goal-plan-stats">
            {/* ONE TOTAL ON THIS CARD, AND IT IS THE ONE THE STEPS TALK ABOUT.
                The strip used to report `coverage.records` — the rows carrying
                structured fields — beside steps counting `total_signals`, so a
                numerate reader met "1,009" and "1,275 signals" on one screen
                with nothing saying they measure different things. They do, and
                the difference is an implementation detail of the structural
                pass; the reader's question is how much evidence there is. */}
            <Stat label="Signals" value={plan.total_signals.toLocaleString()} />
            {coverage.earliest && coverage.latest ? (
              coverage.earliest === coverage.latest ? (
                // A POINT, NOT A RANGE. "2026-04 → 2026-04" reads as a bug
                // even though it is the honest answer, and it is usually the
                // same fact the dating step explains: every date is the
                // import. Said as one date, it stops looking like an error.
                <Stat label="All dated" value={coverage.earliest} />
              ) : (
                <Stat label="Window" value={`${coverage.earliest} → ${coverage.latest}`} />
              )
            ) : null}
            <Stat label="Sources" value={String(kept.length)} />
            {gaps.length ? (
              <Stat label="Out of reach" value={String(gaps.length)} />
            ) : null}
          </dl>
        ) : null}
      </section>

      {/* ── THE COUNTING UNIT. One serif sentence, because it is the one
          decision every number below inherits. It reads as a statement the
          reader can disagree with, not a switch they have to operate. */}
      <section className="ga-plan-section" data-testid="goal-plan-unit">
        <p className={s.unit}>
          Everything is sized in {plan.currency || "accounts"}.
        </p>
        {plan.account_value_derived_note ? (
          <p className={s.unitNote}>{plan.account_value_derived_note}</p>
        ) : null}
      </section>

      {/* THE DEFINITION AS A QUOTED STATEMENT, not a textarea.
          I9 is unchanged — a definition is adopted or elicited, never
          inferred, and it must stay editable at the moment of approval. What
          changed is that the box no longer greets the reader: the words are a
          quotation with the accent rule, and rewording them is one quiet
          control away. An open field mid-page asked a reader to fill something
          in while they were still trying to read. */}
      {plan.definition_text ? (
        <section className="ga-plan-section" data-testid="goal-plan-definition">
          <h2 className="ga-doc-h3">
            {settled || plan.definition_adopted
              ? "What this was asked to establish"
              : "What this is taken to mean"}
          </h2>
          {settled || plan.definition_adopted || !rewording ? (
            <>
              <blockquote className="ga-doc-quote">
                {definitionEdit ?? plan.definition_text}
              </blockquote>
              {settled || plan.definition_adopted ? null : (
                <button
                  type="button"
                  className={s.quietToggle}
                  onClick={() => setRewording(true)}
                >
                  Reword this
                </button>
              )}
            </>
          ) : (
            <>
              <p className="ga-doc-note">
                {plan.definition_source
                  ? <>Taken from {plan.definition_source}. Change it if that is not what you meant.</>
                  : <>Change it if that is not what you meant.</>}
              </p>
              <textarea
                className="ga-plan-definition-edit"
                aria-label="What this goal means"
                rows={3}
                value={definitionEdit ?? plan.definition_text}
                onChange={(e) => setDefinitionEdit(e.target.value)}
              />
              {plan.definition_note ? (
                <p className="ga-doc-note" data-testid="goal-plan-definition-note">
                  {plan.definition_note}
                </p>
              ) : null}
            </>
          )}
        </section>
      ) : null}

      {/* ── THE STEPS. The centre of the screen. ─────────────────────── */}
      <section className="ga-plan-section" data-testid="goal-plan-approach">
        <h2 className="ga-doc-h3">
          {settled ? "The approach you approved" : "How I will do it"}
        </h2>
        {/* ONLY WHEN THERE IS SOMETHING TO SHOW. On a plan stored before the
            server composed the method every `why` is empty, so this rendered,
            flipped its own `aria-expanded` and its own label, and changed
            nothing else on the card — measured live at exactly the same height
            before and after the click. A control that changes only its own
            state is worse than no control: it tells the reader there is more
            to read and then withholds it. */}
        {steps.some((x) => x.why) ? (
          <button
            type="button"
            className={s.showHow}
            aria-expanded={showHow}
            onClick={() => setShowHow(!showHow)}
          >
            {showHow ? "Hide how" : "Show how"}
          </button>
        ) : null}
        <ol className={s.steps} data-testid="goal-plan-steps">
          {steps.map((step, i) => {
            // The part heading prints once, when it changes. Numbering does
            // NOT restart under it: the number is the reader's handle on the
            // whole method, and five sequences of four is not one method.
            const newPart = step.part && step.part !== steps[i - 1]?.part
            return (
              <React.Fragment key={`${step.n}-${step.primitive || i}`}>
                {newPart ? (
                  <li aria-hidden className={s.partHead}>{step.part}</li>
                ) : null}
                <li className={s.step}>
                  <span className={s.stepN}>{step.n}</span>
                  <span className={s.stepWhat}>{step.what}</span>
                  {step.items?.length ? (
                    <ul className={s.stepItems}>
                      {step.items.map((item, j) => <li key={j}>{item}</li>)}
                    </ul>
                  ) : null}
                  {showHow && step.why ? (
                    <span className={s.stepWhy}>{step.why}</span>
                  ) : null}
                </li>
              </React.Fragment>
            )
          })}
        </ol>
      </section>

      {/* ── WHAT WILL BE READ. A tick states it; changing it is a
          disclosure away, because a reader reading a plan is not filling in a
          form and should not be handed checkboxes while they do it. */}
      <section className="ga-plan-section" data-testid="goal-plan-sources">
        <h2 className="ga-doc-h3">What I will read</h2>
        {plan.sources.length ? (
          <>
            <ul className={s.tickList}>
              {plan.sources.map((src) => {
                // READ FROM WHAT WAS POSTED on a settled plan. Reading local
                // state here rendered every source as kept, so the record
                // silently agreed with a wider run than the one that actually
                // happened — the single thing this card exists to prevent.
                const on = !effectiveExcluded.has(src.source_type)
                return (
                  <li key={src.source_type} className={s.tickRow}>
                    {changingSources && !settled ? (
                      <input
                        type="checkbox"
                        checked={on}
                        aria-label={`Read ${src.label}`}
                        onChange={() => toggle(src.source_type)}
                      />
                    ) : (
                      <span
                        className={on ? s.tick : `${s.tick} ${s.tickOff}`}
                        aria-hidden
                      >
                        {on ? "✓" : "—"}
                      </span>
                    )}
                    {/* A STABLE HOOK FOR "this one was dropped", so the
                        guarantee outlives the styling. A test pinned to a
                        class name breaks the moment the styles move, and
                        proves nothing about what the reader sees. */}
                    <span
                      className={on ? undefined : s.tickLabelOff}
                      data-dropped={on ? undefined : "true"}
                    >
                      <b>{src.label}</b>{" "}
                      <span className="ga-doc-source-count">
                        {src.signal_count.toLocaleString()}
                      </span>
                      {!on ? " — dropped by you" : ""}
                    </span>
                    <span className={s.tickWitness}>
                      Can witness {src.witnesses}
                    </span>
                  </li>
                )
              })}
            </ul>
            {settled ? null : (
              <button
                type="button"
                className={s.quietToggle}
                onClick={() => setChangingSources(!changingSources)}
              >
                {changingSources ? "Done" : "Change what gets read"}
              </button>
            )}
          </>
        ) : (
          <p className="ga-empty" data-testid="goal-plan-no-sources">
            Nothing is connected for this to read.
          </p>
        )}
      </section>

      {/* WHAT THE PLAN STILL NEEDS — a forward reference, not the questions
          themselves. They come after the approval, because they are about the
          plan that was approved. */}
      {settled || !questionCount ? null : (
        <p className="ga-doc-note" data-testid="goal-plan-needs">
          After you approve this I will ask you {questionCount} thing
          {questionCount === 1 ? "" : "s"} I cannot work out from your sources.
        </p>
      )}

      {gaps.length ? (
        <section className="ga-plan-section" data-testid="goal-plan-gaps">
          <h2 className="ga-doc-h3">What I will not be able to answer</h2>
          <ul className="ga-doc-gaps">
            {gaps.map((g, i) => (
              <li key={i}>
                <p className="ga-doc-gap-q">{g.question}</p>
                <p className="ga-doc-gap-why">Because {g.because}.</p>
                <p className="ga-doc-gap-fix">
                  <span className="ga-sources-label">To close it</span>{" "}
                  {g.remedy}
                </p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className={s.stat}>
      <dt className={s.statLabel}>{label}</dt>
      <dd className={s.statValue}>{value}</dd>
    </div>
  )
}

/** One question, with everything the server derived about why it is being
 *  asked.
 *
 *  THE OLD CARD DROPPED ANYTHING IT DID NOT RECOGNISE — `questionInputs[q.id]`
 *  with a `return null` when the id was not one of three. Every question the
 *  reconnaissance pass derived therefore vanished silently, which is the worst
 *  of the three possible behaviours: the reader is not asked, and nobody can
 *  see that they were not asked. This renders whatever arrives.
 */
function QuestionCard({
  question, value, onChange,
}: {
  question: GoalPlanQuestion
  value: string
  onChange: (v: string) => void
}) {
  const options = question.options ?? []
  return (
    <div className={s.question} data-testid={`goal-plan-question-${question.id}`}>
      <p className={s.questionPrompt}>{question.prompt}</p>
      {question.what_i_saw ? (
        <p className={s.questionSaw}>{question.what_i_saw}</p>
      ) : question.why ? (
        <p className={s.questionSaw}>{question.why}</p>
      ) : null}
      {/* WHAT THE ANSWER CHANGES. The server has always populated this — "who
          the recommendation is addressed to", "the date on the decision box" —
          and the card never rendered it, so the one line saying why a question
          is worth answering was the line that never arrived. */}
      {question.affects ? (
        <p className={s.questionAffects}>Changes {question.affects}.</p>
      ) : null}

      {options.length ? (
        // A CLOSED SET RENDERS AS CHOICES. A text box beside two known options
        // invites a third that nothing downstream can read.
        <div className={s.options} role="group" aria-label={question.prompt}>
          {options.map((opt) => (
            <button
              key={opt}
              type="button"
              className={value === opt ? `${s.option} ${s.optionOn}` : s.option}
              aria-pressed={value === opt}
              // A SECOND PRESS CLEARS IT. Otherwise a misclick is permanent
              // and the reader has answered something they did not mean.
              onClick={() => onChange(value === opt ? "" : opt)}
            >
              {opt}
            </button>
          ))}
        </div>
      ) : (
        <input
          type="text"
          className={s.freeText}
          inputMode={question.id === "account_value" ? "decimal" : undefined}
          aria-label={question.prompt}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      )}

      {question.default_if_skipped ? (
        // NEVER "we will guess". Every default is a stated, conservative
        // behaviour the run carries out and discloses.
        <p className={s.questionDefault}>
          If you skip this: {question.default_if_skipped}
        </p>
      ) : null}
    </div>
  )
}

export default GoalAnalysisPlan
