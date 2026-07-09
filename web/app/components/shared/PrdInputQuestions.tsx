"use client"

/**
 * PRD "User input needed" → chat messages with answer buttons.
 *
 * The prd-author skill writes a "User input needed" section into the PRD as
 * decorative HTML. A backend extraction pass lifts each item into a structured
 * question (`prdApi.listInputQuestions`); this component renders the PENDING ones
 * as agent-style chat messages in the PRD's chat thread — an [ESCALATE] product
 * decision shows its candidate answers as buttons, a [NEED] data item shows a
 * free-text box (mirrors the design-agent ClarifyingQuestionSurface split).
 *
 * Answering (`prdApi.answerInputQuestion`) folds the answer into ONLY the affected
 * PRD sections via a scoped backend edit (not a full regeneration), saved as an
 * undoable version. On success the component:
 *   - marks the question answered locally (it flips to a resolved line),
 *   - clears the PRD's local edit drafts (so the fresh server HTML wins), and
 *   - hands the updated PRD up via `onPrdUpdated` so the panel refreshes live.
 *
 * Testability split (mirrors ClarifyingQuestionSurface): pure markup in
 * `PrdInputQuestionCard` (SSR-renderable under node-env vitest), I/O in the
 * container. Uses the global `bc-*` chat classes so the cards read as messages;
 * question-specific bits use a scoped `piq-*` class family.
 */

import { useCallback, useEffect, useState } from "react"
import {
  prdApi,
  type PrdInputQuestion,
  type PrdRecord,
} from "../../lib/api"
import { markdownToPrdState } from "../../lib/prd-adapter"
import type { PrdState } from "../../types/content"
import { IconSparkle } from "./app-icons"

// The PRD's local edit drafts are keyed by prd_id in PrdPanelContent /
// PrdHtmlView. After a scoped edit we clear them so the panel shows the new
// server document rather than a stale in-progress draft.
function clearPrdDrafts(prdId: number) {
  try {
    localStorage.removeItem(`sprntly_prd_html_draft_${prdId}`)
    localStorage.removeItem(`sprntly_prd_draft_${prdId}`)
  } catch {
    /* ignore — best-effort */
  }
}

/** Build the ContentContext PrdState from the API's returned PRD record — same
 *  shape PrdPanelContent uses on load, so the panel re-renders identically. */
export function prdStateFromRecord(rec: PrdRecord): PrdState {
  return {
    ...markdownToPrdState(rec.payload_md),
    prd_id: rec.id,
    figma_file_key: undefined,
    llmPart: rec.llm_part,
    briefId: rec.brief_id,
    insightIndex: rec.insight_index,
  }
}

/** One human-readable line for the sections an answer changed, e.g.
 *  "Updated Requirements and Goal." Falls back to a generic line when the editor
 *  reported no section names. Pure → unit-testable. */
export function changedSectionsLine(sections: string[]): string {
  const names = sections.filter((s) => s && s.trim())
  if (names.length === 0) return "Updated the PRD."
  if (names.length === 1) return `Updated ${names[0]}.`
  const head = names.slice(0, -1).join(", ")
  return `Updated ${head} and ${names[names.length - 1]}.`
}

// ---- pure view --------------------------------------------------------------

export type PrdInputQuestionCardProps = {
  question: PrdInputQuestion
  busy?: boolean
  /** The answer currently being submitted, so the picked option can be marked
   *  active while the (slow, ~1 min) scoped edit runs. */
  pendingAnswer?: string | null
  error?: string | null
  /** Resolution line shown once answered (e.g. the changed-sections summary). */
  resolvedLine?: string | null
  answerText: string
  onAnswerTextChange: (value: string) => void
  onChoose: (choice: string) => void
  onSubmitText: () => void
}

/** Pure presentational card for one question — no hooks, no I/O. Renders the
 *  agent chrome + prompt, then EITHER option buttons (escalate) OR a free-text
 *  answer box (need). Once answered it renders a compact resolved line instead. */
export function PrdInputQuestionCard({
  question,
  busy = false,
  pendingAnswer = null,
  error = null,
  resolvedLine = null,
  answerText,
  onAnswerTextChange,
  onChoose,
  onSubmitText,
}: PrdInputQuestionCardProps) {
  const isAnswered = question.status === "answered"
  const hasChoices = question.tag === "escalate" && question.options.length > 0
  const tagLabel = question.tag === "escalate" ? "DECISION" : "INPUT"

  return (
    <div className="bc-turn piq-turn" data-testid="prd-input-question">
      <div className="bc-agent-head">
        <span className="bc-agent-mark">
          <IconSparkle size={14} />
        </span>
        <span className="bc-agent-name">Sprntly</span>
        <span className="bc-agent-badge">
          <IconSparkle size={10} />
          {tagLabel}
        </span>
      </div>
      <div className="bc-agent-body">
        <div className="piq-prompt" data-testid="prd-input-question-prompt">
          {question.prompt}
        </div>
        {question.owner ? (
          <div className="piq-owner">owner: {question.owner}</div>
        ) : null}

        {isAnswered ? (
          <div className="piq-resolved" data-testid="prd-input-question-resolved">
            <span className="piq-resolved-check" aria-hidden>
              ✓
            </span>
            <span className="piq-resolved-answer">{question.answer}</span>
            {resolvedLine ? (
              <span className="piq-resolved-line"> — {resolvedLine}</span>
            ) : null}
          </div>
        ) : hasChoices ? (
          <div className="piq-choices" data-testid="prd-input-question-choices">
            {question.options.map((opt, i) => {
              const active = busy && pendingAnswer === opt.label
              return (
                <button
                  key={`${i}-${opt.label}`}
                  type="button"
                  className={`bc-action-btn piq-choice${active ? " piq-choice--active" : ""}`}
                  data-testid="prd-input-question-choice"
                  disabled={busy}
                  aria-busy={active}
                  onClick={() => onChoose(opt.label)}
                  title={opt.description ?? undefined}
                >
                  {opt.label}
                </button>
              )
            })}
          </div>
        ) : (
          <form
            className="piq-form"
            data-testid="prd-input-question-form"
            onSubmit={(e) => {
              e.preventDefault()
              onSubmitText()
            }}
          >
            <textarea
              className="piq-input"
              data-testid="prd-input-question-input"
              value={answerText}
              placeholder="Provide the answer…"
              onChange={(e) => onAnswerTextChange(e.target.value)}
              disabled={busy}
            />
            <button
              type="submit"
              className="bc-action-btn bc-action-btn--primary"
              data-testid="prd-input-question-submit"
              disabled={busy || !answerText.trim()}
            >
              {busy ? "Updating PRD…" : "Answer"}
            </button>
          </form>
        )}

        {busy && !isAnswered ? (
          <div
            className="piq-applying"
            role="status"
            aria-live="polite"
            data-testid="prd-input-question-applying"
          >
            <span className="piq-applying-spinner" aria-hidden />
            <span>Applying your answer — folding it into the PRD (this can take a minute)…</span>
          </div>
        ) : null}

        {error ? (
          <div className="piq-error" role="alert" data-testid="prd-input-question-error">
            {error}
          </div>
        ) : null}
      </div>
    </div>
  )
}

// ---- container --------------------------------------------------------------

export type PrdInputQuestionsProps = {
  prdId: number
  /** Called with the updated PRD after a successful answer so the host can push
   *  it into ContentContext + its tab cache and refresh the panel live. */
  onPrdUpdated?: (prd: PrdState) => void
  /** Injected for tests; fall back to the real api methods (resolved lazily so an
   *  incomplete api mock in a host's test can never crash render). */
  listQuestions?: (prdId: number) => Promise<PrdInputQuestion[]>
  answerQuestion?: typeof prdApi.answerInputQuestion
}

/**
 * Public component. Loads the PRD's input questions and renders each pending one
 * as an agent-style chat message with answer affordances. Answering routes
 * through the scoped-edit endpoint and hands the updated PRD up via
 * `onPrdUpdated`. Renders nothing when there are no questions.
 */
export function PrdInputQuestions({
  prdId,
  onPrdUpdated,
  listQuestions,
  answerQuestion,
}: PrdInputQuestionsProps) {
  const [questions, setQuestions] = useState<PrdInputQuestion[]>([])
  const [answerText, setAnswerText] = useState<Record<number, string>>({})
  const [busyId, setBusyId] = useState<number | null>(null)
  const [pendingAnswer, setPendingAnswer] = useState<string | null>(null)
  const [errorId, setErrorId] = useState<{ id: number; msg: string } | null>(null)
  const [resolvedLines, setResolvedLines] = useState<Record<number, string>>({})

  useEffect(() => {
    // Input questions are a best-effort enhancement: if the endpoint errors, the
    // chat simply shows no questions — never crashes. The real api method is
    // resolved INSIDE the promise chain so even a throwing access (e.g. an
    // incomplete api mock in a host's test) is caught rather than crashing render.
    let cancelled = false
    Promise.resolve()
      .then(() => (listQuestions ?? prdApi.listInputQuestions)(prdId))
      .then((qs) => {
        if (!cancelled) setQuestions(qs)
      })
      .catch(() => {
        if (!cancelled) setQuestions([])
      })
    return () => {
      cancelled = true
    }
  }, [prdId, listQuestions])

  const submit = useCallback(
    async (question: PrdInputQuestion, rawAnswer: string) => {
      const answer = rawAnswer.trim()
      if (!answer || busyId != null) return
      setBusyId(question.id)
      setPendingAnswer(answer)
      setErrorId(null)
      try {
        const answerFn = answerQuestion ?? prdApi.answerInputQuestion
        const res = await answerFn(prdId, question.id, answer)
        // Flip the question to answered locally (it renders as a resolved line).
        setQuestions((prev) =>
          prev.map((q) => (q.id === question.id ? res.question : q)),
        )
        setResolvedLines((prev) => ({
          ...prev,
          [question.id]: changedSectionsLine(res.sections_changed),
        }))
        // The scoped edit produced a fresh document — drop stale local drafts so
        // the panel shows the server copy, then hand the PRD up to refresh it.
        clearPrdDrafts(prdId)
        onPrdUpdated?.(prdStateFromRecord(res.prd))
      } catch (e) {
        setErrorId({
          id: question.id,
          msg: e instanceof Error ? e.message : "Could not apply your answer",
        })
      } finally {
        setBusyId(null)
        setPendingAnswer(null)
      }
    },
    [prdId, busyId, answerQuestion, onPrdUpdated],
  )

  // Render pending questions as actionable, and questions answered in THIS
  // session as resolved lines (so the chat confirms the change). Questions that
  // were already answered before this mount are hidden to keep the thread clean.
  const visible = questions.filter(
    (q) => q.status === "pending" || resolvedLines[q.id] != null,
  )
  if (visible.length === 0) return null

  return (
    <div className="piq-list" data-testid="prd-input-questions">
      {visible.map((q) => (
        <PrdInputQuestionCard
          key={q.id}
          question={q}
          busy={busyId === q.id}
          pendingAnswer={busyId === q.id ? pendingAnswer : null}
          error={errorId?.id === q.id ? errorId.msg : null}
          resolvedLine={resolvedLines[q.id] ?? null}
          answerText={answerText[q.id] ?? ""}
          onAnswerTextChange={(v) =>
            setAnswerText((prev) => ({ ...prev, [q.id]: v }))
          }
          onChoose={(choice) => submit(q, choice)}
          onSubmitText={() => submit(q, answerText[q.id] ?? "")}
        />
      ))}
    </div>
  )
}
