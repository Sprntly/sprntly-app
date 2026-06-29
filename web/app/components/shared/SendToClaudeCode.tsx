"use client"

import { useState } from "react"
import { prdApi } from "../../lib/api"

/**
 * "Send to Claude Code" action for a PRD.
 *
 * Triggers ON-DEMAND generation of the machine-readable Implementation Spec for
 * the PRD (the backend generates it the first time and caches it until the human
 * PRD changes), then copies the agent-ready spec to the clipboard so the user can
 * paste it into Claude Code. Shows a loading state while the spec generates.
 *
 * This replaces the old always-visible "LLM-readable" PRD tab: the machine spec
 * is no longer a viewable surface — it is produced only at the moment of handoff.
 */
export function SendToClaudeCode({
  prdId,
  onToast,
}: {
  prdId: number
  /** Surface a toast (title, body). Wired to NavigationContext.showToast by the
   *  parent so this component stays context-free and easily testable. */
  onToast: (title: string, body: string) => void
}) {
  const [sending, setSending] = useState(false)

  const send = async () => {
    if (sending) return
    setSending(true)
    try {
      const res = await prdApi.sendToClaudeCode(prdId)
      const spec = res.llm_part?.trim() ?? ""
      if (spec) {
        try {
          await navigator.clipboard.writeText(spec)
        } catch {
          /* clipboard may be unavailable; the spec was still generated */
        }
        onToast(
          "Ready for Claude Code",
          "Implementation spec generated and copied to your clipboard — paste it into Claude Code.",
        )
      } else {
        onToast("Nothing to send", "The implementation spec came back empty.")
      }
    } catch {
      onToast("Couldn't generate spec", "Sending to Claude Code failed. Try again.")
    } finally {
      setSending(false)
    }
  }

  return (
    <button
      type="button"
      className="prd-send-claude-btn"
      data-testid="prd-send-claude"
      disabled={sending}
      onClick={send}
    >
      <svg
        width="13"
        height="13"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <polyline points="16 18 22 12 16 6" />
        <polyline points="8 6 2 12 8 18" />
      </svg>
      {sending ? "Generating spec…" : "Send to Claude Code"}
    </button>
  )
}
