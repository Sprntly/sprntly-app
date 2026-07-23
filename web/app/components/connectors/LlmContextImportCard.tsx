"use client"

import { useEffect, useRef, useState } from "react"
import { llmContextApi } from "../../lib/api"

/**
 * Settings → Connectors: bring context in from your own AI assistant.
 *
 * The Settings-side counterpart to the onboarding import step, for the user
 * who skipped it — or who has since had a lot more conversations worth
 * importing. Same two moves: copy the prompt, upload the `.md` it produces.
 *
 * Deliberately NOT a workspace-field prefill here. During onboarding the user
 * is walking through every field and reviews each one; in Settings those
 * fields are already filled and quietly overwriting them would be hostile. So
 * the upload files the export as a document source (grounding the agents) and
 * reports exactly what it read — nothing is silently changed.
 */
export function LlmContextImportCard() {
  const [prompt, setPrompt] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    let cancelled = false
    llmContextApi
      .prompt()
      .then((r) => !cancelled && setPrompt(r.prompt))
      .catch(() => !cancelled && setPrompt(null))
    return () => {
      cancelled = true
    }
  }, [])

  async function copyPrompt() {
    if (!prompt) return
    try {
      await navigator.clipboard.writeText(prompt)
      setCopied(true)
      setTimeout(() => setCopied(false), 2500)
    } catch {
      setStatus("Couldn't copy — select the prompt text and copy it manually.")
    }
  }

  async function onPick(file: File | null) {
    if (!file) return
    setBusy(true)
    setStatus(null)
    try {
      const res = await llmContextApi.importFile(file)
      const read = Object.keys(res.fields).length
      setStatus(
        res.ok
          ? `Imported ${read} field${read === 1 ? "" : "s"} of context from "${file.name}". It's saved to your documents and available to the AI.`
          : (res.note ??
            "We couldn't read that file. Make sure it's the .md our prompt produced."),
      )
    } catch (e) {
      setStatus(
        e instanceof Error ? e.message : `Couldn't read "${file.name}".`,
      )
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ""
    }
  }

  return (
    <div className="set-conn-upload set-conn-llm-context">
      <i className="ti ti-sparkles" aria-hidden />
      Import context from your AI
      <span className="muted">
        Run our prompt in Claude, ChatGPT or Gemini, then upload the .md
      </span>
      <span className="set-conn-llm-actions">
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => void copyPrompt()}
          disabled={!prompt || busy}
        >
          {copied ? "Copied" : "Copy prompt"}
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => fileRef.current?.click()}
          disabled={busy}
        >
          {busy ? "Reading…" : "Upload .md"}
        </button>
      </span>
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
