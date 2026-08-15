"use client"

/**
 * QuestionPopup — the chat's one way of ASKING the user something.
 *
 * Requested from a live session with a screenshot of Claude's own question
 * surface: when the assistant needs answers (the clarify-first gate before a
 * generation, a PRD's "User input needed" items, "which ticket / which person"
 * during an assignment), the questions should NOT land in the thread as a long
 * scrollable card — they pop up over the bottom of the chat as a stepper:
 * ONE question at a time, its options as buttons, a category chip, `‹ 1/2 ›`
 * pagination, Skip — click, click, click and the batch is sent.
 *
 * SENT ONCE, AT THE END — the owner's second directive, after testing a
 * version that submitted each answer as it was clicked: "let me finish all
 * the questions before you submit." Clicks are local; the user can page back
 * and change any answer; nothing leaves the popup until the LAST question
 * settles, then `onComplete` fires exactly once with the whole record (skips
 * included). Whatever applying-state the submission needs lives in the host,
 * not here.
 *
 * Presentation only beyond that: the popup owns no network calls and no
 * question content. Hosts own both, which is what keeps it testable and lets
 * three different flows share it. Rendered in the chat's dock (above the
 * composer), so it sits "on top of the chat" without covering the thread the
 * user may need to re-read to answer.
 */

import { useLayoutEffect, useRef, useState } from "react"

/** How many option rows the list shows before it starts scrolling. Reported
 *  from a live session: "assign some of the tickets to me" listed all ten of a
 *  PRD's tickets, and the popup grew tall enough to bury the composer and the
 *  thread it was asking about. */
const MAX_VISIBLE_OPTIONS = 5

export type PopupOption = {
  label: string
  /** One quiet line under the label — what picking this option means. */
  description?: string | null
  /** Stable id for hosts whose labels can collide (two tickets sharing a
   *  title). Rides back on the answer; display never uses it. */
  value?: string
}

export type PopupQuestion = {
  /** Small-caps category chip ("TARGET PRODUCT"). Falls back to the host's
   *  `fallbackHeader`, then to "QUESTION". */
  header?: string | null
  prompt: string
  options: PopupOption[]
  /** The assumption this question falls back to when skipped — stated up
   *  front, same contract as the clarify card it replaces. */
  skipDefault?: string | null
  /** Free-text escape hatch. Defaults on: an option list is the assistant's
   *  guess at the answer space, never the whole of it. Hosts whose options
   *  ARE the whole answer space (a team roster) turn it off. */
  allowOther?: boolean
  /** The QUESTION admits several answers at once ("which tickets should X
   *  get?") — options toggle like checkboxes and a Confirm button settles the
   *  question with every pick on `PopupAnswer.picks`. The question's OWN
   *  semantics decide this, host-side, never a popup-wide switch: a batch can
   *  mix single-pick decisions with one multi-pick roster question. Skip and
   *  the settled-once-then-submit contract are unchanged. Reported from a
   *  live session: "assign 2 tickets to fortune" listed the tickets but only
   *  one could be clicked. */
  multiSelect?: boolean
}

export type PopupAnswer = {
  prompt: string
  answer: string
  skipped: boolean
  /** The picked option's `value`, when it carried one. Absent for typed
   *  answers and skips. */
  value?: string
  /** Multi-select questions only: every picked option, in display order.
   *  `answer` carries the joined labels for the thread's human record; hosts
   *  that act on the answer read THIS, one action per pick. */
  picks?: { label: string; value?: string }[]
}

export type QuestionPopupProps = {
  questions: PopupQuestion[]
  fallbackHeader?: string
  /** Host is busy (the submitted batch is being applied) — inert. */
  busy?: boolean
  /** Fires exactly once, when the last question settles. Every question is
   *  accounted for: unanswered ones ride as `skipped`. */
  onComplete: (answers: PopupAnswer[]) => void
  /** Footer "skip the rest" affordance (the clarify gate's "Generate now").
   *  Hidden when absent. */
  onSkipAll?: () => void
  /** Label the skip-all button carries (e.g. "Generate now"). */
  skipAllLabel?: string
  /** Collapse the popup and answer in the composer instead. Hidden when
   *  absent. */
  onDismiss?: () => void
  /** Answers already settled in a PREVIOUS sitting, keyed by question index —
   *  the stepper seeds them and starts at the first genuinely open question.
   *  How a host makes interruption non-lossy: this popup lives in a dock that
   *  higher-priority batches can claim mid-batch and unmounts on tab
   *  switches, and before this prop a five-question batch answered four deep
   *  lost everything, silently, every time (the reported "I answered these
   *  over and over" bug). Presentation contract unchanged — the HOST owns
   *  the storage; this only reads what it is handed. */
  initialAnswers?: Record<number, PopupAnswer>
  /** Fires on EVERY settle (answer or skip) with the question's index — the
   *  host's hook for persisting the draft `initialAnswers` restores. NOT a
   *  submit: nothing leaves the popup until `onComplete`, exactly as before
   *  (the owner's finish-everything-first directive holds). */
  onProgress?: (index: number, answer: PopupAnswer) => void
}

/** Next index without a settled answer, looking forward from `from` then
 *  wrapping to the earliest unanswered — so Back-then-answer never strands a
 *  question. -1 when everything is settled. */
function nextOpen(
  settled: Record<number, PopupAnswer>,
  count: number,
  from: number,
): number {
  for (let i = from + 1; i < count; i++) if (!settled[i]) return i
  for (let i = 0; i < count; i++) if (!settled[i]) return i
  return -1
}

export function QuestionPopup({
  questions,
  fallbackHeader = "Question",
  busy = false,
  onComplete,
  onSkipAll,
  skipAllLabel = "Skip all",
  onDismiss,
  initialAnswers,
  onProgress,
}: QuestionPopupProps) {
  // Seeded from the previous sitting's draft (lazy initializers — read once
  // per mount, exactly like fresh state). The start index is the first
  // question WITHOUT a restored answer, so a resumed batch picks up where the
  // interruption happened instead of re-asking from 1/N.
  const [index, setIndex] = useState(() => {
    const seeded = initialAnswers ?? {}
    const first = nextOpen(seeded, questions.length, -1)
    return first === -1 ? 0 : first
  })
  const [settled, setSettled] = useState<Record<number, PopupAnswer>>(
    () => ({ ...(initialAnswers ?? {}) }),
  )
  const [otherOpen, setOtherOpen] = useState<Record<number, boolean>>({})
  const [typed, setTyped] = useState<Record<number, string>>({})
  // Multi-select questions: the in-progress ticks, per question index, BEFORE
  // Confirm settles them. Seeded from a restored answer's picks so Back (and
  // a resumed draft) shows the boxes as they were left. Kept separate from
  // `settled` on purpose — ticking is browsing, confirming is answering, and
  // only the confirm advances the stepper.
  const [multiPicked, setMultiPicked] = useState<Record<number, PopupOption[]>>(
    () => {
      const seeded: Record<number, PopupOption[]> = {}
      for (const [k, a] of Object.entries(initialAnswers ?? {})) {
        if (a?.picks?.length) seeded[Number(k)] = a.picks.map((p) => ({ ...p }))
      }
      return seeded
    },
  )
  // `onComplete` must fire exactly once even though settling state and firing
  // it happen across renders.
  const [completed, setCompleted] = useState(false)
  // The scrolling option list: the measured height of the first
  // MAX_VISIBLE_OPTIONS rows, and whether the user has scrolled to the last one
  // (the bottom fade lifts there).
  const optionsRef = useRef<HTMLDivElement | null>(null)
  const [optionsCap, setOptionsCap] = useState<number | null>(null)
  const [atEnd, setAtEnd] = useState(false)

  const count = questions.length
  const q = questions[Math.min(index, count - 1)]

  const optionCount = q?.options.length ?? 0
  const scrolls = optionCount > MAX_VISIBLE_OPTIONS

  // Measure rather than guess. Rows are a bare label for one host, a label
  // plus a ticket id for another, and two wrapped lines for a long title, so
  // any px constant shows four rows on one question and seven on the next.
  // Reading where the fifth row ends is the only definition of "five rows"
  // that survives all three.
  useLayoutEffect(() => {
    const el = optionsRef.current
    if (!el || !scrolls) {
      setOptionsCap(null)
      return
    }
    const measure = () => {
      const rows = Array.from(el.children) as HTMLElement[]
      const first = rows[0]
      const last = rows[MAX_VISIBLE_OPTIONS - 1]
      if (!first || !last) return
      // Both rows share an offsetParent, so the difference is the run of five
      // rows plus the four gaps between them wherever the popup sits. Zero
      // under jsdom, which lays nothing out — stay uncapped there.
      const h = last.offsetTop + last.offsetHeight - first.offsetTop
      setOptionsCap(h > 0 ? h : null)
    }
    measure()
    // A narrower popup rewraps long labels; a late font swap changes line
    // height. Neither moves the CAPPED container's own box, so watch the first
    // row as well as the list.
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null
    if (ro) {
      ro.observe(el)
      const first = el.children[0]
      if (first) ro.observe(first)
    }
    return () => ro?.disconnect()
  }, [scrolls, index, optionCount])

  // A new question starts at the top of its own list, not wherever the last
  // one was left scrolled to.
  useLayoutEffect(() => {
    if (optionsRef.current) optionsRef.current.scrollTop = 0
    setAtEnd(false)
  }, [index])

  if (!q || completed) return null

  const record = settled[index]

  const finish = (all: Record<number, PopupAnswer>) => {
    const full = questions.map(
      (qq, i) => all[i] ?? { prompt: qq.prompt, answer: "", skipped: true },
    )
    setCompleted(true)
    onComplete(full)
  }

  const settle = (i: number, answer: PopupAnswer) => {
    const next = { ...settled, [i]: answer }
    setSettled(next)
    // The host's draft hook — BEFORE the possible finish, so the last answer
    // is in the draft even if `onComplete`'s submission then fails.
    onProgress?.(i, answer)
    const open = nextOpen(next, count, i)
    if (open === -1) finish(next)
    else setIndex(open)
  }

  const give = (answer: string, value?: string) => {
    if (busy) return
    const a = answer.trim()
    if (!a) return
    settle(index, { prompt: q.prompt, answer: a, skipped: false, value })
  }

  const skip = () => {
    if (busy) return
    settle(index, { prompt: q.prompt, answer: "", skipped: true })
  }

  const isMulti = !!q.multiSelect
  const picked = multiPicked[index] ?? []
  const isPicked = (opt: PopupOption) =>
    picked.some((p) => (opt.value != null ? p.value === opt.value : p.label === opt.label))

  const togglePick = (opt: PopupOption) => {
    if (busy) return
    setMultiPicked((prev) => {
      const cur = prev[index] ?? []
      const next = isPicked(opt)
        ? cur.filter((p) => (opt.value != null ? p.value !== opt.value : p.label !== opt.label))
        : [...cur, { label: opt.label, ...(opt.value != null ? { value: opt.value } : {}) }]
      return { ...prev, [index]: next }
    })
  }

  const confirmPicks = () => {
    if (busy || picked.length === 0) return
    settle(index, {
      prompt: q.prompt,
      answer: picked.map((p) => p.label).join(", "),
      skipped: false,
      picks: picked.map((p) => ({ ...p })),
    })
  }

  const header = (q.header || "").trim() || fallbackHeader
  // Multi mode never shows the typed escape hatch: a free-text line can't be
  // one of several ticks, and every multi host's options ARE the answer space.
  const showOther = !isMulti && (q.allowOther ?? true) && (q.options.length === 0 || !!otherOpen[index])

  return (
    <div className="qpu" data-testid="question-popup" role="dialog" aria-label={q.prompt}>
      <div className="qpu-head">
        <span className="qpu-chip" data-testid="question-popup-chip">{header}</span>
        <span className="qpu-nav">
          <button
            type="button"
            className="qpu-nav-btn"
            aria-label="Previous question"
            disabled={busy || index === 0}
            onClick={() => setIndex((i) => Math.max(0, i - 1))}
          >
            ‹
          </button>
          <span className="qpu-count" data-testid="question-popup-count">
            {index + 1}/{count}
          </span>
          <button
            type="button"
            className="qpu-nav-btn"
            aria-label="Next question"
            disabled={busy || index >= count - 1}
            onClick={() => setIndex((i) => Math.min(count - 1, i + 1))}
          >
            ›
          </button>
          <button
            type="button"
            className="qpu-skip"
            data-testid="question-popup-skip"
            disabled={busy}
            onClick={skip}
          >
            Skip
          </button>
          {onDismiss ? (
            <button
              type="button"
              className="qpu-close"
              data-testid="question-popup-dismiss"
              aria-label="Answer in the chat instead"
              title="Answer in the chat instead"
              disabled={busy}
              onClick={onDismiss}
            >
              ×
            </button>
          ) : null}
        </span>
      </div>

      <div className="qpu-prompt" data-testid="question-popup-prompt">{q.prompt}</div>

      <div
        ref={optionsRef}
        className={`qpu-options${scrolls ? " qpu-options--scroll" : ""}`}
        data-testid="question-popup-options"
        data-scroll-end={scrolls && atEnd ? "1" : undefined}
        style={optionsCap != null ? { maxHeight: optionsCap } : undefined}
        onScroll={
          scrolls
            ? (e) => {
                const el = e.currentTarget
                setAtEnd(el.scrollTop + el.clientHeight >= el.scrollHeight - 2)
              }
            : undefined
        }
      >
        {q.options.map((opt, oi) => {
          // Single mode: active = the settled answer (Back shows the choice).
          // Multi mode: active = ticked-but-not-yet-confirmed; a click
          // TOGGLES instead of settling, and Confirm below does the settling.
          const active = isMulti
            ? isPicked(opt)
            : !record?.skipped && record?.answer === opt.label
          return (
            <button
              key={`${oi}-${opt.label}`}
              type="button"
              className={`qpu-option${active ? " qpu-option--active" : ""}`}
              data-testid="question-popup-option"
              role={isMulti ? "checkbox" : undefined}
              aria-checked={isMulti ? active : undefined}
              aria-pressed={isMulti ? undefined : active}
              disabled={busy}
              onClick={() => (isMulti ? togglePick(opt) : give(opt.label, opt.value))}
            >
              {isMulti ? (
                <span className="qpu-option-tick" aria-hidden>
                  {active ? "☑" : "☐"}
                </span>
              ) : null}
              <span className="qpu-option-label">{opt.label}</span>
              {opt.description ? (
                <span className="qpu-option-desc">{opt.description}</span>
              ) : null}
            </button>
          )
        })}
        {!isMulti && (q.allowOther ?? true) && q.options.length > 0 ? (
          <button
            type="button"
            className={`qpu-option qpu-option--other${otherOpen[index] ? " qpu-option--active" : ""}`}
            data-testid="question-popup-other"
            aria-expanded={!!otherOpen[index]}
            disabled={busy}
            onClick={() => setOtherOpen((p) => ({ ...p, [index]: !p[index] }))}
          >
            <span className="qpu-option-label">Other…</span>
          </button>
        ) : null}
      </div>

      {isMulti ? (
        <div className="qpu-other-form">
          {/* Ticking is browsing; THIS is the answer. Disabled at zero picks —
              "none of these" is what Skip says. */}
          <button
            type="button"
            className="bc-action-btn bc-action-btn--primary qpu-multi-confirm"
            data-testid="question-popup-confirm-picks"
            disabled={busy || picked.length === 0}
            onClick={confirmPicks}
          >
            {picked.length === 0
              ? "Pick one or more"
              : `Confirm ${picked.length} selected`}
          </button>
        </div>
      ) : null}

      {showOther ? (
        <form
          className="qpu-other-form"
          onSubmit={(e) => {
            e.preventDefault()
            give(typed[index] ?? "")
          }}
        >
          <textarea
            className="qpu-input"
            data-testid="question-popup-input"
            value={typed[index] ?? ""}
            placeholder="Type your answer…"
            disabled={busy}
            onChange={(e) => setTyped((p) => ({ ...p, [index]: e.target.value }))}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                give(typed[index] ?? "")
              }
            }}
          />
          <button
            type="submit"
            className="bc-action-btn bc-action-btn--primary qpu-other-submit"
            data-testid="question-popup-submit-text"
            disabled={busy || !(typed[index] ?? "").trim()}
          >
            Answer
          </button>
        </form>
      ) : null}

      {q.skipDefault ? (
        <div className="qpu-skip-hint">if skipped, I&apos;ll assume: {q.skipDefault}</div>
      ) : null}

      {onSkipAll ? (
        <div className="qpu-foot">
          <button
            type="button"
            className="qpu-skip-all"
            data-testid="question-popup-skip-all"
            disabled={busy}
            onClick={onSkipAll}
          >
            {skipAllLabel}
          </button>
        </div>
      ) : null}
    </div>
  )
}
