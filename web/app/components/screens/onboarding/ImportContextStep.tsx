"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../../../lib/auth"
import { OnboardingChrome } from "../../onboarding/OnboardingChrome"
import { useOnboarding } from "../../../context/OnboardingContext"
import {
  llmContextApi,
  type LlmContextFields,
  type LlmContextImportResponse,
} from "../../../lib/api"
import { applyImportedContext } from "../../../lib/onboarding/applyImportedContext"
import { advanceOnboardingStep, createWorkspace } from "../../../lib/onboarding/store"
import { stepForSlug } from "../../../lib/onboarding/types"
import type { WorkspaceCompany } from "../../../lib/onboarding/types"

/**
 * Onboarding step 02 — "Import your context" (client feedback, 2026-07-22;
 * moved behind the company step again 2026-07-27).
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
 * ONE READ PER UPLOAD, IN THE BACKGROUND, and that is why nothing here blocks.
 * The POST files the .md as a company document and hands back a job id; a
 * single LLM extraction reads the file, and it reads documents of ANY shape (a
 * file our prompt produced, an edited or reworded one, a strategy doc the user
 * already had). There is deliberately no instant heading parse alongside it —
 * see backend/app/llm_context.py for why that reader was removed with the v3
 * prompt. The round-trip is spent on the steps ahead: connectors and the api
 * key, the two the extraction cannot prefill at all (one wires OAuth, the other
 * takes a secret). The fields land on onboarding context while they work, and
 * every step behind opens pre-filled — each seeds fill-only, so a late arrival
 * pops into a field they haven't typed in and leaves the ones they have alone.
 * "Keep going" is live the moment the upload returns, and an extraction that
 * fails or times out just leaves the later steps to be typed.
 *
 * An import writes ONLY onto fields the workspace has left empty. Later steps
 * already seed their inputs from `workspace`, so the user reviews and edits
 * every imported value on the step that owns it — an import prefills a form,
 * it never commits an answer on the user's behalf.
 *
 * IT RUNS SECOND, BEHIND `company`, AND THAT ORDER IS LOAD-BEARING (2026-07-27).
 * The prompt opens with a confirmed-values block naming the company and its
 * website, and the assistant treats those as the entity to search for. Run
 * first, this step had to hand that block over empty and hope the user retyped
 * it; run second, the backend writes both in from what they just entered, so
 * the assistant starts with the entity locked. The trade is that the company
 * step is the one step an import can no longer prefill — a fair price for the
 * document being about the right company. (It led the flow 2026-07-25 to 07-27
 * for exactly the opposite reason.)
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
  ["team_name", "workspace name"],
  ["team_scope", "team scope"],
  ["sizing_methodology", "sizing"],
]

function summarise(fields: LlmContextFields): string[] {
  return FIELD_LABELS.filter(([key]) => {
    const value = fields[key]
    return Array.isArray(value) ? value.length > 0 : Boolean(value)
  }).map(([, label]) => label)
}

export function ImportContextStep() {
  const auth = useAuth()
  const {
    workspace,
    setWorkspace,
    loading,
    contextImport,
    contextImportFields,
    startContextImport,
  } = useOnboarding()
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

  // What step 1 collected. The backend writes these into the prompt's
  // confirmed-values block, which is the whole reason `company` runs first.
  const companyName = workspace?.display_name ?? ""
  const companyWebsite = workspace?.product?.website ?? ""

  // Fetch the prompt from the backend rather than duplicating it here, so the
  // text the user pastes can never drift from what the extraction reads back —
  // and so the block is filled in one place for every caller (this step and the
  // Settings card both).
  //
  // Waits for `loading` to clear: firing on mount would ask for the prompt
  // before the workspace resolves and serve one with the company lines empty.
  useEffect(() => {
    if (loading) return
    let cancelled = false
    llmContextApi
      .prompt({ companyName, companyWebsite })
      .then((r) => {
        if (cancelled) return
        setPrompt(r.prompt)
        // Never clobber an edit in progress: the draft is only re-seeded while
        // it still holds exactly what the server last served (or nothing yet).
        setPromptDraft((draft) => (!draft || draft === prompt ? r.prompt : draft))
      })
      .catch(() => {
        // Non-fatal: the copy button falls back to disabled with a hint, and
        // the manual path still works.
        if (!cancelled) setPrompt(null)
      })
    return () => {
      cancelled = true
    }
    // `prompt` is read inside the setter only, to compare against the draft —
    // depending on it would refetch on every fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, companyName, companyWebsite])

  /** Merge imported values onto the workspace, leaving anything the user has
   *  already filled in untouched. Shared with the background extraction's
   *  apply path, so both reads of the file write through the same rules. */
  async function applyFields(target: WorkspaceCompany, fields: LlmContextFields) {
    setWorkspace(await applyImportedContext(target, fields))
  }

  /**
   * The company row the upload needs — `/llm-context/import` is tenant-scoped.
   * Normally it already exists: the company step runs first and creates it. This
   * is the deep-link safety net (someone lands here without passing step 1),
   * which would otherwise 403 on the first file they pick. `display_name` stays
   * blank on purpose: the import fills it when the document names the company,
   * and the company step (which requires a name) collects it otherwise. A
   * guessed placeholder would arrive there looking like the user's own answer.
   */
  async function ensureWorkspace(): Promise<WorkspaceCompany | null> {
    if (workspace) return workspace
    if (auth.kind !== "authed") return null
    const created = await createWorkspace({
      companyName: "",
      // Blank → no product row yet (products_name_nonempty); the import and
      // the product step both upsert one once something names it.
      productName: "",
      accountType: "company",
      userId: auth.user.id,
      onboardingStep: stepForSlug("company") ?? 1,
    })
    setWorkspace(created)
    return created
  }

  async function onPickFile(file: File | null) {
    if (!file) return
    setError(null)
    setBusy(true)
    try {
      const target = await ensureWorkspace()
      if (!target) {
        setError("Sign in again to import your context.")
        return
      }
      const response = await llmContextApi.importFile(file)
      setResult(response)
      // `ok` is false on every upload now — the extraction is the only reader
      // and it has not run yet. Kept rather than dropped so a future
      // synchronous read, and an older job result replayed through the same
      // shape, still apply here instead of silently doing nothing.
      if (response.ok) {
        try {
          await applyFields(target, response.fields)
        } catch {
          // The read succeeded; only the write failed. Say so honestly
          // instead of reporting an import that didn't land anywhere.
          setError(
            "We read your context but couldn't save it to your workspace. Try again, or continue and fill the steps in manually.",
          )
        }
      }
      // Hand the background LLM pass to the provider, which outlives this
      // screen — it keeps polling while the user works through connectors and
      // merges whatever it finds onto the workspace when it lands. This is the
      // read, not a second opinion on one: without it the upload prefills
      // nothing.
      if (response.job_id) {
        startContextImport(response.job_id, target.id)
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
   *  whole point of it running in the background, and connectors + api-key
   *  cover its latency. */
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

  // What to name back to the user. The upload response carries fields only in
  // the degenerate case above; normally they arrive with the extraction, so
  // prefer whichever read actually produced something.
  const importedFields = contextImportFields ?? (result?.ok ? result.fields : null)
  const imported = importedFields ? summarise(importedFields) : []
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

      {imported.length > 0 && (
        <div className="onb-import-result" role="status">
          <strong>Context imported.</strong> We pre-filled your{" "}
          {imported.join(", ")} — you&apos;ll review each one on the next few
          steps.
          {result?.filed && (
            <p className="onb-field-hint">
              We also saved the file itself to your documents, so the AI can use
              everything in it, not just the fields above.
            </p>
          )}
        </div>
      )}

      {/* Read but empty. The file reached the knowledge graph either way, so
          this is "nothing for the form", not "we lost your file". */}
      {contextImport === "done" && imported.length === 0 && !error && (
        <div className="onb-import-result" role="status">
          <strong>We read your file, but couldn&apos;t fill anything in.</strong>{" "}
          It&apos;s saved to your documents so the AI can still use it — the
          steps ahead are yours to fill in.
        </div>
      )}

      {/* The read itself, which is explicitly NOT something to wait on — so the
          copy points at the exit rather than at a spinner. */}
      {extracting && !error && (
        <div className="onb-import-result" role="status">
          <strong>Reading your file…</strong> Keep going — we&apos;ll fill in
          whatever we find while you check your company details, and it&apos;ll
          be waiting on the steps after.
        </div>
      )}

      {/* The pitch, stated plainly and up front. People skim past a card
          titled "Copy a prompt for your own AI" without registering that the
          prompt is meant for the assistant they ALREADY use every day — this
          says so before they reach the buttons. */}
      <div className="ctx-import-lead">
        <i className="ti ti-sparkles" aria-hidden />
        <span className="ctx-import-lead-copy">
          <strong className="ctx-import-lead-title">
            Already use Claude or ChatGPT for work?
          </strong>
          <span className="ctx-import-lead-text">
            Copy the prompt below and paste it into Claude or ChatGPT. It will
            extract the context it already has about your business into a file
            you can download and upload here — bringing that context into
            Sprntly so you can shorten your onboarding.
          </span>
        </span>
      </div>

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
              {companyName && (
                <>
                  {" "}
                  It already names <strong>{companyName}</strong>, so your
                  assistant looks for the right company.
                </>
              )}
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
