"use client"

import { useState } from "react"
import {
  normalizeChoice,
  type PendingQuestion,
  type PendingQuestionChoice,
} from "../../lib/api"
import { IconSparkle, IconArrowRight } from "../shared/app-icons"

export type ClarifyingQuestionCardProps = {
  question: PendingQuestion
  busy?: boolean
  /** Continue with the chosen label / write-your-own text. */
  onAnswer: (answer: string) => void | Promise<void>
  /** Skip — dismiss the question without iterating. */
  onSkip: () => void | Promise<void>
}

/** Structured clarifying-question CARD shown inline in the iterate activity
 *  stream when the agent pauses for input. Replaces the bare `InlineClarifyAnswer`
 *  surface. Pure presentational + local selection state only — the continue path
 *  routes through `onAnswer` (existing answer/iterate path), the skip path through
 *  `onSkip` (dismiss endpoint, no iterate, no preview reload). */
export function ClarifyingQuestionCard({
  question,
  busy,
  onAnswer,
  onSkip,
}: ClarifyingQuestionCardProps) {
  const normalizedChoices: PendingQuestionChoice[] = (question.choices ?? []).map(
    normalizeChoice,
  )
  const [selected, setSelected] = useState<number | "own" | null>(null)
  const [ownText, setOwnText] = useState("")

  const canContinue =
    selected !== null && (selected !== "own" || ownText.trim().length > 0)

  const handleContinue = () => {
    if (selected === "own") {
      void onAnswer(ownText.trim())
    } else if (typeof selected === "number") {
      void onAnswer(normalizedChoices[selected].label)
    }
  }

  return (
    <div
      className="da-qcard"
      data-testid="da-activity-answer"
      role="region"
      aria-label="Answer the Design Agent"
    >
      <span className="da-qcard-badge">
        <IconSparkle size={12} />
        Needs your input
      </span>
      {question.context && (
        <p className="da-qcard-context">{question.context}</p>
      )}
      <p className="da-qcard-question">{question.question}</p>

      {normalizedChoices.map((choice, i) => {
        const hasDesc = !!choice.description && choice.description.trim().length > 0
        const isSel = selected === i
        return (
          <button
            key={`${i}-${choice.label}`}
            type="button"
            className={"da-qcard-opt" + (isSel ? " da-qcard-opt--sel" : "")}
            data-testid="da-qcard-option"
            data-has-desc={hasDesc ? "true" : "false"}
            disabled={busy}
            onClick={() => setSelected(i)}
          >
            <span className="da-qcard-radio" />
            <span className="da-qcard-opt-body">
              <span className="da-qcard-opt-label">{choice.label}</span>
              {hasDesc && (
                <span
                  className="da-qcard-opt-desc"
                  data-testid="da-qcard-option-desc"
                >
                  {choice.description}
                </span>
              )}
            </span>
          </button>
        )
      })}

      <button
        type="button"
        className={"da-qcard-opt" + (selected === "own" ? " da-qcard-opt--sel" : "")}
        data-testid="da-qcard-option-own"
        disabled={busy}
        onClick={() => setSelected("own")}
      >
        <span className="da-qcard-radio" />
        <span className="da-qcard-opt-body">
          <span className="da-qcard-opt-label">Write your own…</span>
        </span>
      </button>
      {selected === "own" && (
        <input
          className="da-qcard-own-input"
          data-testid="da-qcard-own-input"
          placeholder="Describe exactly what you want"
          value={ownText}
          autoFocus
          onChange={(e) => setOwnText(e.target.value)}
        />
      )}

      <div className="da-qcard-foot">
        <button
          type="button"
          className="da-qcard-skip"
          data-testid="da-qcard-skip"
          onClick={() => void onSkip()}
          disabled={busy}
        >
          Skip this change
        </button>
        <span className="da-qcard-spacer" />
        <button
          type="button"
          className="da-qcard-continue btn btn-accent"
          data-testid="da-qcard-continue"
          disabled={busy || !canContinue}
          onClick={handleContinue}
        >
          Continue
          <IconArrowRight size={14} />
        </button>
      </div>
    </div>
  )
}
