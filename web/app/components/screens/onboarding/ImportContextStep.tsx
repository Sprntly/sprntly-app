"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import {
  llmContextApi,
  type LlmContextFields,
  type LlmContextImportResponse,
} from "../../../lib/api"
import { applyImportedContext } from "../../../lib/onboarding/applyImportedContext"
import { advanceOnboardingStep } from "../../../lib/onboarding/store"
import { stepForSlug } from "../../../lib/onboarding/types"

/**
 * Onboarding step 02 — "Import your context" (client feedback, 2026-07-22).
 *
 * The premise: most PMs have already explained their company, product, users
 * and strategy to an assistant many times over. Retyping all of it is the
 * single biggest reason setup stalls. So the step offers a shortcut and an
 * opt-out, and nothing else:
 *
 *   1. COPY THE PROMPT — run it in Claude / ChatGPT / Gemini, upload the .md
 *      it returns, and the rest of onboarding arrives pre-filled.
 *   2. FILL IT IN MANUALLY — skip, and type each step as before.
 *
 * There is deliberately NO "connect your Claude account" path. It was built
 * and removed: an Anthropic OAuth token authorises Messages API calls, and no
 * public endpoint exposes a user's claude.ai conversation history — so the
 * connected account could not produce the context the button promised, and a
 * card that always failed on click was worse than not offering it. One
 * copy-paste prompt works in every assistant today and needs no registered
 * app on either side.
 *
 * TWO READS PER UPLOAD, and the second is why this step hands off to
 * CONNECTORS rather than product. The POST returns a deterministic heading
 * parse instantly — exact, but it only understands files our own prompt
 * produced. It also kicks a background LLM extraction that reads context
 * documents of ANY shape (an edited export, a reworded one, a strategy doc
 * the user already had). That pass costs a round-trip, so the user spends it
 * on the one step the import cannot prefill: connecting their tools. The
 * fields land on onboarding context while they work, and metrics/product open
 * pre-filled. Nothing here blocks on it — "Keep going" is live the moment the
 * upload returns, and an extraction that fails or times out just leaves the
 * later steps to be typed.
 *
 * An import writes ONLY onto fields the workspace has left empty, on both
 * passes. Later steps already seed their inputs from `workspace`, so the user
 * reviews and edits every imported value on the step that owns it — an import
 * prefills a form, it never commits an answer on the user's behalf.
 */

/** Ordered for the summary line: the fields worth naming back to the user. */
const FIELD_LABELS: Array<[keyof LlmContextFields, string]> = [
  ["company_name", "company"],
  ["mission", "mission"],
  ["strategy", "strategy"],
  ["portfolio", "portfolio"],
  ["planning_cycle", "planning cycle"],
  ["product_name", "product"],
  ["surfaces", "surfaces"],
  ["monetization", "monetization"],
  ["users_description", "users"],
  ["competitors", "competitors"],
  ["metrics", "metrics"],
  ["prioritization_framework", "prioritization"],
  ["team_scope", "team scope"],
]

function summarise(fields: LlmContextFields): string[] {
  return FIELD_LABELS.filter(([key]) => {
    const value = fields[key]
    return Array.isArray(value) ? value.length > 0 : Boolean(value)
  }).map(([, label]) => label)
}

export function ImportContextStep() {
  const { workspace, setWorkspace, loading, contextImport, startContextImport } =
    useOnboarding()
  const router = useRouter()

  /** The prompt as the backend serves it — the baseline we reset back to. */
  const [prompt, setPrompt] = useState<string | null>(null)
  /** What the user will actually copy. Seeded from `prompt`, then editable:
   *  people want to add a project name, narrow the scope, or drop a section
   *  before running it in their assistant, and forcing them to paste-then-edit
   *  on the far side loses those tweaks next time they come back. */
  const [promptDraft, setPromptDraft] = useState("")
  /** Whether the prompt text is revealed. Copying is offered inside the
   *  revealed panel, so the user sees (and can adjust) what they're pasting
   *  before they take it to their assistant. */
  const [promptOpen, setPromptOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<LlmContextImportResponse | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)

  // Fetch the prompt from the backend rather than duplicating it here, so the
  // text the user pastes can never drift from what the parser reads back.
  useEffect(() => {
    let cancelled = false
    llmContextApi
      .prompt()
      .then((r) => {
        if (cancelled) return
        setPrompt(r.prompt)
        setPromptDraft(r.prompt)
      })
      .catch(() => {
        // Non-fatal: the copy button falls back to disabled with a hint, and
        // the manual path still works.
        if (!cancelled) setPrompt(null)
      })
    return () => {
      cancelled = true
    }
  }, [])

  /** Merge imported values onto the workspace, leaving anything the user has
   *  already filled in untouched. Shared with the background extraction's
   *  apply path, so both reads of the file write through the same rules. */
  async function applyFields(fields: LlmContextFields) {
    if (!workspace) return
    setWorkspace(await applyImportedContext(workspace, fields))
  }

  async function onPickFile(file: File | null) {
    if (!file) return
    setError(null)
    setBusy(true)
    try {
      const response = await llmContextApi.importFile(file)
      setResult(response)
      if (response.ok) {
        try {
          await applyFields(response.fields)
        } catch {
          // The parse succeeded; only the write failed. Say so honestly
          // instead of reporting an import that didn't land anywhere.
          setError(
            "We read your context but couldn't save it to your workspace. Try again, or continue and fill the steps in manually.",
          )
        }
      }
      // Hand the background LLM pass to the provider, which outlives this
      // screen — it keeps polling while the user works through connectors and
      // merges whatever it finds onto the workspace when it lands. Kicked even
      // when the heading parse read nothing, because reading the files that
      // parse cannot is the whole point of the second pass.
      if (response.job_id && workspace) {
        startContextImport(response.job_id, workspace.id)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : `Couldn't read "${file.name}".`)
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ""
    }
  }

  async function copyPrompt() {
    // Copy what's on screen, edits included — not the pristine server copy.
    const text = promptDraft.trim() ? promptDraft : prompt
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2500)
    } catch {
      setError("Couldn't copy — select the prompt text and copy it manually.")
    }
  }

  /** Leave the step. Never awaits the background extraction — that is the
   *  whole point of it running in the background, and connectors is the step
   *  chosen to cover its latency. */
  function advance() {
    const ws = workspace
    if (ws) {
      // Fire-and-forget, and deliberately fully contained: `onboarding_step` is
      // a resume hint, not a gate, so nothing about stamping it — including a
      // synchronous throw — may stand between the user and the next step.
      void (async () => {
        try {
          setWorkspace(
            await advanceOnboardingStep(ws.id, stepForSlug("connectors") ?? 3),
          )
        } catch {
          /* they land on connectors either way; resume just re-derives it */
        }
      })()
    }
    router.push("/onboarding/connectors")
  }

  if (loading) return <div className="onb-shell">Loading…</div>

  const imported = result?.ok ? summarise(result.fields) : []
  const extracting = contextImport === "running"
  const promptEdited = prompt !== null && promptDraft !== prompt

  return (
    <OnboardingChrome
      step={2}
      title={
        <>
          Import your <em>context.</em>
        </>
      }
      subtitle="The fastest way to set up: hand over the context you've already given your AI assistant, and the rest of setup arrives pre-filled for you to review. Nothing is shared — it stays in your workspace."
      footerMeta="Import context — optional"
      onBack={() => router.push("/onboarding/company")}
      onContinue={advance}
      continueLabel={result ? "Keep going" : "Skip for now"}
      loading={busy}
    >
      {error && <div className="onb-form-error">{error}</div>}

      {result?.note && !error && (
        <div className="onb-form-error" role="status">
          {result.note}
        </div>
      )}

      {result?.ok && (
        <div className="onb-import-result" role="status">
          <strong>Context imported.</strong> We pre-filled your{" "}
          {imported.join(", ")} — you&apos;ll review each one on the next few
          steps.
          {Object.keys(result.unmapped).length > 0 && (
            <p className="onb-field-hint">
              We also saved {Object.keys(result.unmapped).length} extra section
              {Object.keys(result.unmapped).length === 1 ? "" : "s"} to your
              documents so the AI can use them.
            </p>
          )}
        </div>
      )}

      {/* The second read. Shown whether or not the heading parse found
          anything, because it is the pass that handles a file our own prompt
          didn't produce — and it is explicitly NOT something to wait on, so
          the copy points at the exit rather than at a spinner. */}
      {extracting && !error && (
        <div className="onb-import-result" role="status">
          <strong>
            {result?.ok
              ? "Still reading the rest of your file…"
              : "Reading your file…"}
          </strong>{" "}
          Keep going — we&apos;ll fill in whatever else we find while you
          connect your tools, and it&apos;ll be waiting on the steps after.
        </div>
      )}

      <div className="onb-import-options">
        <div className="onb-import-card is-recommended">
          <span className="onb-import-card-body">
            <span className="onb-import-card-title">
              Copy a prompt for your own AI
            </span>
            <span className="onb-import-card-desc">
              Run our prompt in Claude, ChatGPT or Gemini, then upload the{" "}
              <code>.md</code> it gives you. Works with any assistant — nothing
              to connect.
            </span>
            <span className="onb-import-card-actions">
              {/* Show, then copy. Revealing the prompt before copying lets the
                  user see what they're about to paste into their assistant —
                  a blind "Copy" of an unseen instruction block asks for more
                  trust than this step has earned yet. */}
              <button
                type="button"
                className="btn btn-brand"
                onClick={() => setPromptOpen((open) => !open)}
                disabled={!prompt || busy}
                aria-expanded={promptOpen}
                aria-controls="onb-prompt-panel"
              >
                {promptOpen ? "Hide prompt" : "Show prompt"}
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => fileRef.current?.click()}
                disabled={busy}
              >
                {busy ? "Reading…" : "Upload .md"}
              </button>
            </span>
            {!prompt && (
              <span className="onb-field-hint">
                Couldn&apos;t load the prompt just now — refresh, or continue and
                fill the steps in manually.
              </span>
            )}
          </span>
          <input
            ref={fileRef}
            type="file"
            accept=".md,.markdown,.txt,text/markdown,text/plain"
            style={{ display: "none" }}
            onChange={(e) => void onPickFile(e.target.files?.[0] ?? null)}
            aria-label="Context export"
          />
        </div>

        {promptOpen && prompt && (
          <div className="onb-prompt-panel" id="onb-prompt-panel">
            <div className="onb-prompt-panel-head">
              <i className="ti ti-clipboard-text" aria-hidden />
              Run this in your AI, then upload the <code>.md</code> it returns
            </div>
            {/* Editable: tweak it here and the Copy button takes your
                version. Edits are local to this step — the server copy is
                untouched, and Reset brings it back. */}
            <textarea
              className="onb-prompt-panel-body"
              value={promptDraft}
              onChange={(e) => setPromptDraft(e.target.value)}
              spellCheck={false}
              aria-label="Prompt to run in your AI assistant"
            />
            <div className="onb-prompt-panel-foot">
              <span className="onb-prompt-panel-hint">
                {promptEdited
                  ? "Edited — Copy takes your version."
                  : "Edit it before you copy if you want to narrow the scope."}
              </span>
              {promptEdited && (
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => setPromptDraft(prompt ?? "")}
                >
                  Reset
                </button>
              )}
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => void copyPrompt()}
                disabled={!promptDraft.trim()}
              >
                {copied ? "Copied" : "Copy prompt"}
              </button>
            </div>
          </div>
        )}
      </div>

      <p className="onb-import-manual">
        Prefer to do it yourself?{" "}
        <button type="button" className="onb-linkish" onClick={advance}>
          Fill it in manually
        </button>
      </p>
    </OnboardingChrome>
  )
}
