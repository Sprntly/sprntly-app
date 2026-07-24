"use client"

import { useEffect, useRef, useState } from "react"
import { llmContextApi, type LlmContextJobStatus } from "../../lib/api"
import { pollUntil } from "../../lib/poll"

/**
 * Settings → Business Context: bring context in from your own AI assistant.
 *
 * The Settings-side counterpart to the onboarding import step, for the user
 * who skipped it — or who has since had a lot more conversations worth
 * importing. Same two moves: copy the prompt, upload the `.md` it produces.
 * It lives on Business Context (not Connectors) because a whole-company import
 * feeds the business-context lens and the rest of the workspace fields, so it
 * belongs with the context it grounds rather than in the data-source list.
 *
 * Deliberately NOT a workspace-field prefill here. During onboarding the user
 * is walking through every field and reviews each one; in Settings those
 * fields are already filled and quietly overwriting them would be hostile. So
 * the upload files the export as a document source (grounding the agents) and
 * reports exactly what it read — nothing is silently changed.
 */
export function LlmContextImportCard() {
  const [prompt, setPrompt] = useState<string | null>(null)
  /** What the user actually copies. Seeded from `prompt`, then editable — people
   *  like to add a project name or narrow the scope before running it, and
   *  making them edit on the far side loses that next time. Mirrors onboarding. */
  const [promptDraft, setPromptDraft] = useState("")
  /** Whether the editable prompt panel is revealed. Copy lives inside it, so the
   *  user sees (and can adjust) exactly what they're about to paste. */
  const [promptOpen, setPromptOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)
  // Flipped on unmount so a still-running reader-2 poll stops touching state.
  const unmounted = useRef(false)

  useEffect(() => {
    let cancelled = false
    llmContextApi
      .prompt()
      .then((r) => {
        if (cancelled) return
        setPrompt(r.prompt)
        setPromptDraft(r.prompt)
      })
      .catch(() => !cancelled && setPrompt(null))
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(
    () => () => {
      unmounted.current = true
    },
    [],
  )

  async function copyPrompt() {
    // Copy what's on screen, edits included — not the pristine server copy.
    const text = promptDraft.trim() ? promptDraft : prompt
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2500)
    } catch {
      setStatus("Couldn't copy — select the prompt text and copy it manually.")
    }
  }

  /** The success line once we know how much of the file we understood. The raw
   *  .md is filed as a Company Document and ingested into the knowledge graph on
   *  upload regardless, so this is never a failure — at most we understood no
   *  structured fields and the document still grounds the agents. */
  function importedLine(fileName: string, fieldCount: number): string {
    return fieldCount > 0
      ? `Read ${fieldCount} field${fieldCount === 1 ? "" : "s"} of context from "${fileName}". It's saved to your Company Documents and feeding your knowledge graph.`
      : `Added "${fileName}" to your Company Documents — it's feeding your knowledge graph.`
  }

  /** Poll reader 2 — the background LLM pass — and upgrade the status once it
   *  lands. Reader 1 (the deterministic heading parse) only understands files
   *  our own prompt produced; an edited or reworded export reads as empty to it
   *  and fine to the LLM, so this is what makes an arbitrary context document
   *  count. Unlike onboarding we do NOT prefill any form from the result — we
   *  only report what we understood. Runs in the background (the KG feed already
   *  happened at upload), never rejects, and no-ops if the card unmounts. */
  async function refineWithReader2(jobId: number, fileName: string) {
    let final: LlmContextJobStatus
    try {
      final = await pollUntil<LlmContextJobStatus>({
        fetchStatus: () => llmContextApi.importStatus(jobId),
        isDone: (v) => v.status !== "generating",
        maxMs: 180 * 1000,
        intervalMs: 2000,
        isCancelled: () => unmounted.current,
      })
    } catch {
      // A poll blip doesn't undo the ingest — the first message stands.
      return
    }
    if (unmounted.current) return
    const fields =
      final.status === "ready" && final.result
        ? Object.keys(final.result.fields).length
        : 0
    setStatus(importedLine(fileName, fields))
  }

  async function onPick(file: File | null) {
    if (!file) return
    setBusy(true)
    setStatus(null)
    let res
    try {
      // Reader 1 runs inline and returns here. The same call files the .md as a
      // Company Document, ingests it into the knowledge graph, and kicks off
      // reader 2 (the background LLM pass), handing back its job_id.
      res = await llmContextApi.importFile(file)
    } catch (e) {
      if (!unmounted.current) {
        setStatus(e instanceof Error ? e.message : `Couldn't read "${file.name}".`)
        setBusy(false)
      }
      if (fileRef.current) fileRef.current.value = ""
      return
    }
    if (unmounted.current) return
    setBusy(false)
    if (fileRef.current) fileRef.current.value = ""

    // Filing the raw .md + the KG ingest is the whole outcome here (we never
    // prefill). If that failed server-side, say so plainly and don't claim a
    // knowledge-graph feed that didn't happen — the readers are moot then.
    if (res.filed === false) {
      setStatus(
        res.note ??
          `We read "${file.name}" but couldn't save it to your Company Documents, so it isn't in your knowledge graph yet. Try again in a moment.`,
      )
      return
    }

    const read = Object.keys(res.fields).length
    if (res.ok) {
      // Reader 1 already understood the file — report it and we're done.
      setStatus(importedLine(file.name, read))
      return
    }
    // Reader 1 read nothing. The file is already filed + feeding the KG, so
    // confirm that now and let reader 2 upgrade the line if the LLM reads more.
    setStatus(
      `Added "${file.name}" to your Company Documents — it's feeding your knowledge graph. Reading it for a fuller picture…`,
    )
    if (res.job_id != null) {
      void refineWithReader2(res.job_id, file.name)
    } else {
      setStatus(importedLine(file.name, read))
    }
  }

  const promptEdited = prompt !== null && promptDraft !== prompt

  return (
    <div className="set-conn-upload set-conn-llm-context">
      <i className="ti ti-sparkles" aria-hidden />
      Import context from your AI
      <span className="set-conn-llm-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => fileRef.current?.click()}
          disabled={busy}
        >
          {busy ? "Reading…" : "Upload .md"}
        </button>
      </span>
      <span className="muted set-conn-llm-desc">
        Copy the prompt, paste it into your AI tool to generate a .md file, then
        upload that file here — we&apos;ll feed it into your knowledge graph.
      </span>
      <span className="set-conn-llm-show">
        {/* Reveal-then-copy: show the prompt so the user sees (and can edit)
            what they're about to paste into their AI tool before taking it. */}
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => setPromptOpen((open) => !open)}
          disabled={!prompt || busy}
          aria-expanded={promptOpen}
          aria-controls="llm-ctx-prompt-panel"
        >
          {promptOpen ? "Hide prompt" : "Show prompt"}
        </button>
      </span>

      {promptOpen && prompt && (
        <div className="onb-prompt-panel" id="llm-ctx-prompt-panel">
          <div className="onb-prompt-panel-head">
            <i className="ti ti-clipboard-text" aria-hidden />
            Paste this into your AI tool, then upload the <code>.md</code> it
            generates
          </div>
          {/* Editable: tweak it here and Copy takes your version. Edits are
              local — the server copy is untouched, and Reset brings it back. */}
          <textarea
            className="onb-prompt-panel-body"
            value={promptDraft}
            onChange={(e) => setPromptDraft(e.target.value)}
            spellCheck={false}
            aria-label="Prompt to run in your AI tool"
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

      <input
        ref={fileRef}
        type="file"
        accept=".md,.markdown,.txt,text/markdown,text/plain"
        style={{ display: "none" }}
        onChange={(e) => void onPick(e.target.files?.[0] ?? null)}
        aria-label="AI context export"
      />
      {status && (
        <span className="muted" role="status">
          {status}
        </span>
      )}
    </div>
  )
}
