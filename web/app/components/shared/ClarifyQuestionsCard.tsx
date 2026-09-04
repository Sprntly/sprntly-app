"use client"

/**
 * The clarify-first gate's questions, as ONE answerable card.
 *
 * "Generate a PRD for X" runs a sufficiency check (POST /v1/prd/clarify-task)
 * that can come back with 3–5 targeted questions, each carrying 2–4 candidate
 * `options` and a `skip_default`. Those questions used to be flattened into a
 * numbered markdown list inside a normal agent bubble — the structure the
 * backend went to the trouble of producing was thrown away, and answering meant
 * re-typing prose that addressed four questions at once.
 *
 * This renders them the way Claude's terminal asks a batch: every question laid
 * out in sequence with its options as buttons, an "Other…" escape hatch for a
 * typed answer, and ONE submit at the bottom. Nothing is sent until the user
 * submits, so they can move between questions freely and answer only what they
 * know — anything left blank falls back to that question's stated assumption.
 *
 * Answering in the composer still works exactly as before (the card is an extra
 * affordance, not a replacement), so the intro keeps pointing at it.
 *
 * Testability split mirrors PrdInputQuestionCard: this file is pure markup +
 * local UI state, no I/O — the host owns the network call.
 */

import { useMemo, useState } from "react"
import { IconSparkle } from "./app-icons"

export type ClarifyQuestion = {
  prompt: string
  options: string[]
  skip_default?: string | null
  /** No defensible default exists — the material this asks for is absent, so
   *  proceeding would invent it. Set by the clarify gate, never by the user.
   *  While one of these is unanswered the card refuses to generate: the bug
   *  this exists to stop was a PRD written about a call whose transcript never
   *  arrived, from a `skip_default` that literally read "cannot proceed". */
  blocking?: boolean
  /** 2–3 word category label ("Target users") — worn as the chip when the
   *  batch renders in the QuestionPopup stepper. Optional: older backends and
   *  persisted threads predate it. */
  header?: string | null
}

/** One resolved answer, paired with the question it answers — the host renders
 *  these into the "Additional details from the user:" block the generator sees. */
export type ClarifyAnswer = { prompt: string; answer: string }

/** How a settled batch was settled — the resolved card states each outcome
 *  truthfully rather than showing a blank where an answer never existed.
 *   - "card": submitted from here; questions left blank fell to their defaults.
 *   - "chat": answered in prose in the composer, so no per-question mapping
 *     exists — the reply itself is the answer and renders below.
 *   - "skip": "Generate now"; every question fell to its default. */
export type ClarifyMode = "card" | "chat" | "skip"

export type ClarifyResolution = { answers: ClarifyAnswer[]; mode: ClarifyMode }

export type ClarifyQuestionsCardProps = {
  questions: ClarifyQuestion[]
  /** True once the answers have been submitted and generation is starting — the
   *  whole card goes inert rather than unmounting mid-click. */
  busy?: boolean
  /** Set once the batch is settled: the card stops taking input and becomes a
   *  READ-ONLY record of what was decided.
   *
   *  It deliberately keeps the same shape it had while open — numbering, one
   *  block per question — instead of collapsing back to the flattened list.
   *  Answered questions are the audit trail of what the PRD was built from, so
   *  they are worth MORE structure once settled, not less. */
  resolved?: ClarifyResolution
  onSubmit: (answers: ClarifyAnswer[]) => void
  /** "Generate now" — proceed with the original task, every question skipped. */
  onSkip: () => void
}

// The DURABLE form of the clarify gate's questions: a flattened numbered list.
//
// What the user actually sees is the interactive card above (options as
// buttons, one submit for the batch) — this text is what gets PERSISTED as the
// assistant turn and what a reloaded thread falls back to, since the card's
// live answering machinery is transient. Shared by every host that renders
// this card (main via `ChatScreen`'s re-export, the private project engine) so
// the durable form and the live card can never drift against each other.
export function clarifyQuestionsText(questions: ClarifyQuestion[]): string {
  const lines = questions.map((q, i) => {
    const opts = q.options.length ? ` (e.g. ${q.options.join(" / ")})` : ""
    // Skips are informed, not silent: each question states the assumption the
    // author proceeds with when it goes unanswered.
    const skip = q.blocking
      ? " — I can't assume this one; without it there is nothing to write from"
      : q.skip_default
        ? ` — if skipped, I'll assume: ${q.skip_default}`
        : ""
    // Blank-line separated: single newlines are soft-wrapped away by the
    // markdown renderer, which ran the whole list together on one line.
    return `${i + 1}. ${q.prompt}${opts}${skip}`
  })
  // The invitation to say "generate now" is DROPPED when anything is blocking.
  // The durable turn is what a reloaded thread reads and what the user acts on,
  // so offering an escape that the card refuses would just move the confusion.
  const anyBlocking = questions.some((q) => q.blocking)
  const opener = anyBlocking
    ? "Before I write this PRD I need the following — I can't fill these in myself:"
    : "Before I write this PRD, a few details would make it much stronger. " +
      'Answer what you can in one message — or say "generate now" and I\'ll ' +
      "proceed with what I have:"
  return `${opener}\n\n${lines.join("\n\n")}`
}

// The user's card answers, rendered as the message they'd otherwise have typed.
// Q/A pairs (not bare answers) so the generator can tell which question each one
// settles — the composer path sends prose, and this must be at least as legible.
export function clarifyAnswersText(answers: ClarifyAnswer[]): string {
  return answers.map((a) => `${a.prompt}\n${a.answer}`).join("\n\n")
}

/** The answer a question currently holds: the picked option, or the typed text
 *  when the free-text box is open and non-empty (typing beats a stale pick). */
function resolveAnswer(picked: string | undefined, typed: string | undefined): string {
  const t = (typed ?? "").trim()
  if (t) return t
  return (picked ?? "").trim()
}

/** The settled batch, read-only: same numbering and per-question blocks it had
 *  while open, each showing either the answer given or the assumption it fell
 *  back to. Pure — no state, no handlers. */
function ResolvedQuestions({
  questions,
  resolution,
}: {
  questions: ClarifyQuestion[]
  resolution: ClarifyResolution
}) {
  const byPrompt = new Map(resolution.answers.map((a) => [a.prompt, a.answer]))
  const answeredCount = resolution.answers.length
  // Three distinct states — the "fell back" clause is only true when questions
  // were actually left blank. Claiming it after a full answer set contradicts
  // the per-question ✓ marks right below it.
  const summary =
    resolution.mode === "chat"
      ? "Answered in your reply below."
      : answeredCount === 0
        ? `Generated without these — each fell back to the assumption below.`
        : answeredCount >= questions.length
          ? `All ${questions.length} answered.`
          : `${answeredCount} of ${questions.length} answered — the rest fell back to their assumptions.`

  return (
    <div className="cqc-card cqc-card--resolved" data-testid="clarify-questions-resolved">
      <div className="cqc-intro cqc-intro--resolved">{summary}</div>
      <ol className="cqc-list">
        {questions.map((q, i) => {
          const answer = byPrompt.get(q.prompt)
          return (
            <li
              className={`cqc-q cqc-q--resolved${answer ? " cqc-q--answered" : ""}`}
              key={`${i}-${q.prompt}`}
              data-testid="clarify-question"
            >
              <div className="cqc-q-head">
                <span className="cqc-q-num" aria-hidden>
                  {i + 1}
                </span>
                <span className="cqc-q-prompt">{q.prompt}</span>
              </div>
              {answer ? (
                <div className="cqc-answer" data-testid="clarify-answer">
                  <span className="cqc-answer-check" aria-hidden>
                    ✓
                  </span>
                  <span className="cqc-answer-text">{answer}</span>
                </div>
              ) : (
                // Never render a blank where no answer exists. An unanswered
                // question did not vanish — it resolved to its stated default,
                // and the document was written on that basis.
                <div className="cqc-answer cqc-answer--skipped" data-testid="clarify-answer-skipped">
                  {resolution.mode === "chat"
                    ? "Answered in the reply below"
                    : q.skip_default
                      ? `Skipped — assumed: ${q.skip_default}`
                      : "Skipped"}
                </div>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}

export function ClarifyQuestionsCard({
  questions,
  busy = false,
  resolved,
  onSubmit,
  onSkip,
}: ClarifyQuestionsCardProps) {
  const [picked, setPicked] = useState<Record<number, string>>({})
  const [typed, setTyped] = useState<Record<number, string>>({})
  // Questions that carry options hide the free-text box behind "Other…" so the
  // options lead; a question with none shows it outright.
  const [otherOpen, setOtherOpen] = useState<Record<number, boolean>>({})

  const answers = useMemo<ClarifyAnswer[]>(
    () =>
      questions
        .map((q, i) => ({ prompt: q.prompt, answer: resolveAnswer(picked[i], typed[i]) }))
        .filter((a) => a.answer.length > 0),
    [questions, picked, typed],
  )
  const answeredCount = answers.length

  const anyBlockingUnanswered = questions.some(
    (q, i) => q.blocking && !resolveAnswer(picked[i], typed[i]),
  )

  // Generating with NOTHING answered asks first — the same rule the
  // QuestionPopup applies, because this card is the surface the popup falls
  // back to when it is dismissed, and an escape hatch that skips the question
  // the other surface asks is not a fallback, it is a hole.
  const [confirmingSkip, setConfirmingSkip] = useState(false)

  const submit = () => {
    if (busy) return
    // Nothing answered is a skip in everything but name — route it to the same
    // place so the generator gets the original task rather than an empty
    // "Additional details" block that reads as deliberate emptiness.
    if (answeredCount === 0) setConfirmingSkip(true)
    else onSubmit(answers)
  }

  // Settled → the read-only record. After the hooks, never before them, so the
  // hook order is identical on the render where the card flips resolved.
  if (resolved) return <ResolvedQuestions questions={questions} resolution={resolved} />

  return (
    <div className="cqc-card" data-testid="clarify-questions">
      <div className="cqc-intro">
        Before I write this PRD, a few details would make it much stronger. Answer
        what you can below — or say &quot;generate now&quot; and I&apos;ll proceed
        with what I have:
      </div>

      <ol className="cqc-list">
        {questions.map((q, i) => {
          const hasChoices = q.options.length > 0
          const showText = !hasChoices || !!otherOpen[i]
          const current = resolveAnswer(picked[i], typed[i])
          return (
            <li className="cqc-q" key={`${i}-${q.prompt}`} data-testid="clarify-question">
              <div className="cqc-q-head">
                <span className="cqc-q-num" aria-hidden>
                  {i + 1}
                </span>
                <span className="cqc-q-prompt">{q.prompt}</span>
                {current ? (
                  <span className="cqc-q-check" aria-label="answered">
                    ✓
                  </span>
                ) : null}
              </div>

              {hasChoices ? (
                <div className="cqc-choices" data-testid="clarify-choices">
                  {q.options.map((opt, oi) => {
                    const active = picked[i] === opt
                    return (
                      <button
                        key={`${oi}-${opt}`}
                        type="button"
                        className={`bc-action-btn cqc-choice${active ? " cqc-choice--active" : ""}`}
                        data-testid="clarify-choice"
                        aria-pressed={active}
                        disabled={busy}
                        // Click-again deselects: a mis-tap must be undoable
                        // without reloading the whole batch.
                        onClick={() =>
                          setPicked((prev) => ({ ...prev, [i]: active ? "" : opt }))
                        }
                      >
                        {opt}
                      </button>
                    )
                  })}
                  <button
                    type="button"
                    className={`bc-action-btn cqc-choice cqc-choice--other${showText ? " cqc-choice--active" : ""}`}
                    data-testid="clarify-other"
                    aria-expanded={showText}
                    disabled={busy}
                    onClick={() => setOtherOpen((prev) => ({ ...prev, [i]: !prev[i] }))}
                  >
                    Other…
                  </button>
                </div>
              ) : null}

              {showText ? (
                <textarea
                  className="cqc-input"
                  data-testid="clarify-input"
                  value={typed[i] ?? ""}
                  placeholder="Type your answer…"
                  disabled={busy}
                  onChange={(e) =>
                    setTyped((prev) => ({ ...prev, [i]: e.target.value }))
                  }
                  // ⌘/Ctrl+Enter submits the whole batch from any box, so a
                  // keyboard user never has to reach for the button.
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                      e.preventDefault()
                      submit()
                    }
                  }}
                />
              ) : null}

              {/* Skips stay informed: the assumption this question proceeds with
                  when it goes unanswered, stated up front. */}
              {q.blocking ? (
                <div className="cqc-skip-hint" data-testid="clarify-blocking-hint">
                  I can&apos;t assume this one — without it there is nothing to
                  write from.
                </div>
              ) : q.skip_default ? (
                <div className="cqc-skip-hint">
                  if skipped, I&apos;ll assume: {q.skip_default}
                </div>
              ) : null}
            </li>
          )
        })}
      </ol>

      {confirmingSkip ? (
        <div className="cqc-actions" data-testid="clarify-skip-confirm">
          <span className="cqc-count">
            {anyBlockingUnanswered
              ? "Nothing was answered, and the material this needs never arrived — the PRD would be written from your request alone. Generate anyway?"
              : `You didn't answer ${questions.length === 1 ? "the question" : `any of the ${questions.length} questions`}. Generate anyway?`}
          </span>
          <button
            type="button"
            className="bc-action-btn"
            data-testid="clarify-skip-yes"
            disabled={busy}
            onClick={() => !busy && onSkip()}
          >
            Yes, generate
          </button>
          <button
            type="button"
            className="bc-action-btn bc-action-btn--primary"
            data-testid="clarify-skip-no"
            disabled={busy}
            onClick={() => setConfirmingSkip(false)}
          >
            No, let me answer
          </button>
        </div>
      ) : (
        <div className="cqc-actions">
          <button
            type="button"
            className="bc-action-btn bc-action-btn--primary"
            data-testid="clarify-submit"
            disabled={busy}
            onClick={submit}
          >
            {busy ? "Generating PRD…" : "Generate PRD"}
          </button>
          <button
            type="button"
            className="bc-action-btn"
            data-testid="clarify-skip"
            disabled={busy}
            // Routed through the confirmation rather than straight to onSkip:
            // this button IS the "skip everything" gesture the rule is about.
            onClick={() => !busy && setConfirmingSkip(true)}
          >
            Generate now
          </button>
          <span className="cqc-count" data-testid="clarify-count">
            {answeredCount} of {questions.length} answered
          </span>
        </div>
      )}
    </div>
  )
}

/** The card wrapped in the standard agent chrome, for hosts that render it as a
 *  standalone message rather than inside an existing agent body. */
export function ClarifyQuestionsMessage(props: ClarifyQuestionsCardProps) {
  return (
    <div className="bc-turn">
      <div className="bc-agent-head">
        <span className="bc-agent-mark">
          <IconSparkle size={14} />
        </span>
        <span className="bc-agent-name">Sprntly</span>
        <span className="bc-agent-badge">
          <IconSparkle size={10} />
          QUESTIONS
        </span>
      </div>
      <div className="bc-agent-body">
        <ClarifyQuestionsCard {...props} />
      </div>
    </div>
  )
}
