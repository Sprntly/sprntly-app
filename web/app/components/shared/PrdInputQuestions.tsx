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

import { useCallback, useEffect, useMemo, useState } from "react"
import { createPortal } from "react-dom"
import {
  prdApi,
  type PrdInputQuestion,
  type PrdInputQuestionsList,
  type PrdRecord,
} from "../../lib/api"
import { QuestionPopup, type PopupAnswer } from "./QuestionPopup"
import { markdownToPrdState } from "../../lib/prd-adapter"
import type { PrdState } from "../../types/content"
import { IconSparkle } from "./app-icons"

// The PRD's local edit drafts are keyed by prd_id in PrdPanelContent /
// PrdHtmlView. After a scoped edit we clear them so the panel shows the new
// server document rather than a stale in-progress draft.
export function clearPrdDrafts(prdId: number) {
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
    public_id: rec.public_id,
    figma_file_key: undefined,
    llmPart: rec.llm_part,
    briefId: rec.brief_id,
    insightIndex: rec.insight_index,
    source: rec.source,
    artifactTemplateId: rec.artifact_template_id ?? null,
    artifactTemplateName: rec.artifact_template_name ?? null,
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

/** Presentational card for one question — one local UI toggle, no I/O. Renders
 *  the agent chrome + prompt, then selectable option buttons whenever the question
 *  carries options (BOTH decision resolutions and candidate data values), with an
 *  "Other…" button that reveals a free-text box for anything the options miss. A
 *  question with no options falls back to a plain free-text box. Once answered it
 *  renders a compact resolved line instead. */
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
  const hasChoices = question.options.length > 0
  const tagLabel = question.tag === "escalate" ? "DECISION" : "INPUT"
  // When the question has options, the free-text box is hidden behind an "Other…"
  // affordance so options lead; a question with no options shows it outright.
  const [showOther, setShowOther] = useState(false)

  const textForm = (
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
  )

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
          <>
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
              {/* Escape hatch: none of the proposed options fit → type an exact
                  answer. Mirrors the design-agent card's "Write your own…". */}
              <button
                type="button"
                className={`bc-action-btn piq-choice piq-choice--other${showOther ? " piq-choice--active" : ""}`}
                data-testid="prd-input-question-other"
                disabled={busy}
                aria-expanded={showOther}
                onClick={() => setShowOther((v) => !v)}
              >
                Other…
              </button>
            </div>
            {showOther ? textForm : null}
          </>
        ) : (
          textForm
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
   *  incomplete api mock in a host's test can never crash render). A bare
   *  question array (legacy mocks) is accepted alongside the envelope. */
  listQuestions?: (
    prdId: number,
  ) => Promise<PrdInputQuestion[] | PrdInputQuestionsList>
  answerQuestion?: typeof prdApi.answerInputQuestion
  answerQuestionsBatch?: typeof prdApi.answerInputQuestionsBatch
  /** POPUP MODE (the chat's QuestionPopup stepper, docked above the composer).
   *
   *  `undefined` — legacy inline mode: pending questions render as chat
   *  messages, exactly as before this prop existed.
   *  `null` — popup mode, but the dock is owned by a higher-priority batch
   *  (the clarify gate) or not mounted yet: pending questions HOLD (nothing
   *  inline, no popup) until the host hands the dock over.
   *  an element — popup mode, live: pending questions render into it as one
   *  stepper batch; the thread keeps only the resolved ✓ lines. The batch
   *  SUBMITS ONCE, when the last question settles (owner directive: finish
   *  all the questions before anything is sent) — one scoped edit folds every
   *  answer in together. A question SKIPPED in the popup falls back to its
   *  inline card — skipping is "not in a stepper", never "make the question
   *  disappear". */
  popupHost?: HTMLElement | null
}

// While the backend backfills extraction for a PRD opened before its questions
// existed (or one whose generation-time extraction is still running), poll on a
// steady cadence. Extraction is one small LLM call (~seconds); the cap keeps a
// stuck flag from polling forever.
const EXTRACT_POLL_MS = 2500
const EXTRACT_POLL_MAX = 24 // ≈60s

// ── the stepper's draft — what makes interruption non-lossy ──────────────────
//
// The popup batch submits ONCE, when the last question settles (the owner's
// finish-everything-first directive). Before this draft existed, everything up
// to that point lived in component state only — so a tab switch, the dock
// being claimed by a higher-priority batch (clarify/assign), or a panel
// refresh mid-batch silently discarded every answer given, and the next open
// re-asked from 1/N. Answering "over and over" with nothing ever reaching the
// backend was the reported bug, and the six-hour request log confirming ZERO
// answer submissions was the diagnosis.
//
// Every settle (answer or skip) now writes here, keyed by QUESTION ID — ids
// are stable for stored rows, and a re-extraction (a regenerated PRD) mints
// new ids, so its draft entries simply never match and stale drafts die with
// their questions. Cleared when the batch submits.

type QuestionDraft = Record<number, PopupAnswer>

const draftKey = (prdId: number) => `sprntly_prd_qdraft_${prdId}`

function loadQuestionDraft(prdId: number): QuestionDraft {
  try {
    const raw = localStorage.getItem(draftKey(prdId))
    const parsed = raw ? JSON.parse(raw) : null
    return parsed && typeof parsed === "object" ? (parsed as QuestionDraft) : {}
  } catch {
    return {}
  }
}

function saveQuestionDraftEntry(
  prdId: number, questionId: number, answer: PopupAnswer,
) {
  try {
    const draft = loadQuestionDraft(prdId)
    draft[questionId] = answer
    localStorage.setItem(draftKey(prdId), JSON.stringify(draft))
  } catch {
    /* best-effort — a full store just loses the resume, as before */
  }
}

export function clearQuestionDraft(prdId: number) {
  try {
    localStorage.removeItem(draftKey(prdId))
  } catch {
    /* ignore */
  }
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
  answerQuestionsBatch,
  popupHost,
}: PrdInputQuestionsProps) {
  const [questions, setQuestions] = useState<PrdInputQuestion[]>([])
  const [answerText, setAnswerText] = useState<Record<number, string>>({})
  const [busyId, setBusyId] = useState<number | null>(null)
  const [pendingAnswer, setPendingAnswer] = useState<string | null>(null)
  const [errorId, setErrorId] = useState<{ id: number; msg: string } | null>(null)
  const [resolvedLines, setResolvedLines] = useState<Record<number, string>>({})
  // Popup machinery. `batch` is the SNAPSHOT of pending questions the open
  // stepper is walking — snapshotted so state changes mid-batch can't
  // reshuffle the stepper under the user. `popupSkipped` are questions the
  // user skipped in a stepper; they fall back to inline cards.
  // `batchApplying`/`batchError` cover the ONE submit the whole batch makes
  // after its last question settles.
  const popupMode = popupHost !== undefined
  const [batch, setBatch] = useState<PrdInputQuestion[] | null>(null)
  const [popupSkipped, setPopupSkipped] = useState<Record<number, boolean>>({})
  const [batchApplying, setBatchApplying] = useState(false)
  const [batchError, setBatchError] = useState<string | null>(null)

  useEffect(() => {
    // Input questions are a best-effort enhancement: if the endpoint errors, the
    // chat simply shows no questions — never crashes. The real api method is
    // resolved INSIDE the promise chain so even a throwing access (e.g. an
    // incomplete api mock in a host's test) is caught rather than crashing render.
    //
    // `extracting: true` means the backend just scheduled the extraction for this
    // PRD (opened from Artifacts before its questions existed, or a fresh PRD
    // whose extraction still runs) — keep polling until the questions land, so
    // the chat fills in live instead of staying empty until a reopen.
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let polls = 0
    const load = () => {
      Promise.resolve()
        .then(() => (listQuestions ?? prdApi.listInputQuestions)(prdId))
        .then((res) => {
          if (cancelled) return
          const { questions: qs, extracting } = Array.isArray(res)
            ? { questions: res, extracting: false }
            : { questions: res.questions ?? [], extracting: !!res.extracting }
          setQuestions(qs)
          // Skips PERSIST across opens (the draft) — a question skipped in a
          // previous sitting renders as its inline card instead of re-opening
          // the stepper on every open of this PRD, which is the second half
          // of the "they keep coming" bug. Draft entries whose ids no longer
          // exist (a re-extracted set) simply never match.
          const draft = loadQuestionDraft(prdId)
          const persistedSkips: Record<number, boolean> = {}
          for (const q of qs) {
            if (q.status === "pending" && draft[q.id]?.skipped) {
              persistedSkips[q.id] = true
            }
          }
          if (Object.keys(persistedSkips).length) {
            setPopupSkipped((prev) => ({ ...persistedSkips, ...prev }))
          }
          if (extracting && polls < EXTRACT_POLL_MAX) {
            polls += 1
            timer = setTimeout(load, EXTRACT_POLL_MS)
          }
        })
        .catch(() => {
          if (!cancelled) setQuestions([])
        })
    }
    load()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
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

  /** The popup batch's ONE submit: every answered question folds into the PRD
   *  in a single scoped edit. All-or-nothing — on failure nothing was marked
   *  answered server-side, so the whole batch falls back to inline cards for
   *  retry rather than pretending half of it landed. */
  const submitBatch = useCallback(
    async (items: { question: PrdInputQuestion; answer: string }[]) => {
      if (!items.length) return
      setBatchApplying(true)
      setBatchError(null)
      try {
        const batchFn = answerQuestionsBatch ?? prdApi.answerInputQuestionsBatch
        const res = await batchFn(
          prdId,
          items.map((it) => ({ question_id: it.question.id, answer: it.answer })),
        )
        const byId = new Map(res.questions.map((q) => [q.id, q]))
        setQuestions((prev) => prev.map((q) => byId.get(q.id) ?? q))
        const line = changedSectionsLine(res.sections_changed)
        setResolvedLines((prev) => {
          const next = { ...prev }
          for (const it of items) next[it.question.id] = line
          return next
        })
        clearPrdDrafts(prdId)
        onPrdUpdated?.(prdStateFromRecord(res.prd))
      } catch (e) {
        setBatchError(
          e instanceof Error ? e.message : "Could not apply your answers",
        )
        // Back to the inline cards, still pending, answers re-askable.
        setPopupSkipped((prev) => {
          const next = { ...prev }
          for (const it of items) next[it.question.id] = true
          return next
        })
      } finally {
        setBatchApplying(false)
      }
    },
    [prdId, answerQuestionsBatch, onPrdUpdated],
  )

  // Open a stepper batch: popup mode, dock free, nothing open yet, and at
  // least one pending question the user hasn't popup-skipped. The batch is a
  // snapshot — see the state's comment. The id signature (not the array
  // identity, which changes every render) is what gates re-runs.
  const pendingForPopup = useMemo(
    () => questions.filter((q) => q.status === "pending" && !popupSkipped[q.id]),
    [questions, popupSkipped],
  )
  const pendingSig = pendingForPopup.map((q) => q.id).join(",")
  useEffect(() => {
    if (
      popupMode && popupHost && batch == null && !batchApplying &&
      pendingForPopup.length > 0
    ) {
      setBatch(pendingForPopup)
    }
    // Dock taken away mid-batch (a higher-priority popup, a tab switch) → drop
    // the snapshot; nothing was submitted (the batch sends only on completion),
    // so the reopened batch simply asks again from the top.
    if (popupMode && !popupHost && batch != null) {
      setBatch(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- pendingSig stands in for pendingForPopup
  }, [popupMode, popupHost, batch, batchApplying, pendingSig])

  // Render pending questions as actionable, and questions answered in THIS
  // session as resolved lines (so the chat confirms the change). Questions that
  // were already answered before this mount are hidden to keep the thread clean.
  // In popup mode the pending ones live in the stepper instead — inline keeps
  // the resolved record, plus any question skipped OUT of a stepper (skipping
  // means "not in a popup", never "gone").
  const visible = questions.filter(
    (q) =>
      resolvedLines[q.id] != null ||
      (q.status === "pending" && (!popupMode || popupSkipped[q.id])),
  )

  const popupNode =
    popupMode && popupHost && batch && batch.length > 0
      ? createPortal(
          <QuestionPopup
            key={batch.map((q) => q.id).join(",")}
            questions={batch.map((q) => ({
              header: q.tag === "escalate" ? "Decision" : "Input needed",
              prompt: q.prompt,
              options: q.options.map((o) => ({
                label: o.label,
                description: o.description ?? null,
              })),
            }))}
            fallbackHeader="PRD question"
            // The previous sitting's answers, restored by question id — the
            // stepper resumes at the first open question instead of re-asking
            // from 1/N after every interruption (the reported bug).
            initialAnswers={(() => {
              const draft = loadQuestionDraft(prdId)
              const seeded: Record<number, PopupAnswer> = {}
              batch.forEach((q, i) => {
                const entry = draft[q.id]
                if (entry && !entry.skipped && entry.answer) seeded[i] = entry
              })
              return seeded
            })()}
            // Every settle persists — NOT a submit; the one batch submit below
            // is unchanged. This is only what makes an unmount recoverable.
            onProgress={(i, a) => {
              if (batch[i]) saveQuestionDraftEntry(prdId, batch[i].id, a)
            }}
            onComplete={(answers) => {
              // Skips fall back to their inline cards; everything answered
              // goes out as ONE batch, submitted only now — never mid-stepper.
              setPopupSkipped((prev) => {
                const next = { ...prev }
                answers.forEach((a, i) => {
                  if (a.skipped && batch[i]) next[batch[i].id] = true
                })
                return next
              })
              // The draft's ANSWER entries are spent (they ride the submit);
              // its SKIP entries persist so a skipped question stays inline
              // across opens rather than re-opening the stepper forever.
              try {
                const remaining: QuestionDraft = {}
                answers.forEach((a, i) => {
                  if (a.skipped && batch[i]) remaining[batch[i].id] = a
                })
                localStorage.setItem(draftKey(prdId), JSON.stringify(remaining))
              } catch { /* best-effort */ }
              setBatch(null)
              void submitBatch(
                answers.flatMap((a, i) =>
                  !a.skipped && a.answer && batch[i]
                    ? [{ question: batch[i], answer: a.answer }]
                    : [],
                ),
              )
            }}
          />,
          popupHost,
        )
      : null

  if (visible.length === 0 && !popupNode && !batchApplying && !batchError) return null

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
      {/* The batch's ONE submit, in flight — the popup has already closed, so
          this line is what says the answers are landing. */}
      {batchApplying ? (
        <div
          className="piq-applying"
          role="status"
          aria-live="polite"
          data-testid="prd-input-batch-applying"
        >
          <span className="piq-applying-spinner" aria-hidden />
          <span>Applying your answers — folding them into the PRD (this can take a minute)…</span>
        </div>
      ) : null}
      {batchError ? (
        <div className="piq-error" role="alert" data-testid="prd-input-batch-error">
          {batchError} — the questions are back below, nothing was changed.
        </div>
      ) : null}
      {popupNode}
    </div>
  )
}
