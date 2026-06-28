"use client"

import { useEffect, useRef, useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { runPrdGeneration } from "../../../lib/runPrdGeneration"
import { runEvidenceGeneration } from "../../../lib/runEvidenceGeneration"
import { pickDefaultDetailKey } from "../../../lib/brief-adapter"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"
import { EvidenceSections } from "../../shared/EvidenceSections"
import { EvidenceHtmlBrief } from "../../shared/EvidenceHtmlBrief"

export function DetailScreen() {
  const { goTo, setAIBarValue, expandAiPanel, showToast, openContentPanel } = useNavigation()
  const { content, setContent } = useContent()
  const d = content.detail
  const evidence = content.evidence
  const [generatingPrd, setGeneratingPrd] = useState(false)
  const [evidenceState, setEvidenceState] = useState<
    | { kind: "idle" }
    | { kind: "loading" }
    | { kind: "error"; message: string }
  >({ kind: "idle" })
  /** Tracks which (briefId, insightIndex) the current `content.evidence`
   * came from, so we know when to drop it and refetch on detail change. */
  const loadedKeyRef = useRef<string | null>(null)

  // Hydrate `content.detail` from `briefDetails` map when arriving cold.
  useEffect(() => {
    if (content.detail) return
    const key = pickDefaultDetailKey(content.briefDetails ?? {})
    if (!key) return
    const next = content.briefDetails?.[key]
    if (next) setContent({ detail: next })
  }, [content.detail, content.briefDetails, setContent])

  // Auto-fire evidence generation whenever the finding changes. Backend
  // dedupes via find_existing_evidence, so a previously generated doc
  // returns ~instantly; only the first view per (brief, insight) pays
  // the LLM cost.
  useEffect(() => {
    if (!d?.meta) return
    const key = `${d.meta.briefId}:${d.meta.insightIndex}`
    if (loadedKeyRef.current === key && evidence) return
    let cancelled = false
    setEvidenceState({ kind: "loading" })
    setContent({ evidence: null })
    loadedKeyRef.current = key
    runEvidenceGeneration(d.meta)
      .then((result) => {
        if (cancelled) return
        if (!result.ok) {
          setEvidenceState({ kind: "error", message: result.message })
          return
        }
        setContent({ evidence: result.evidence })
        setEvidenceState({ kind: "idle" })
      })
      .catch((e: unknown) => {
        if (cancelled) return
        const msg = e instanceof Error ? e.message : String(e)
        setEvidenceState({ kind: "error", message: msg })
      })
    return () => {
      cancelled = true
    }
  }, [d?.meta?.briefId, d?.meta?.insightIndex, setContent])

  const handleGeneratePrd = async () => {
    if (!d?.meta) {
      showToast("Can't generate PRD", "Open this evidence from the brief first.")
      return
    }
    setGeneratingPrd(true)
    try {
      const result = await runPrdGeneration(d.meta)
      if (!result.ok) {
        showToast("PRD generation failed", result.message.slice(0, 200))
        return
      }
      // Persist the source pointer so the PRD rail can refetch / regenerate
      // against the same brief insight, then open it in the content panel
      // (same rail card as Evidence — no separate PRD page).
      setContent({ prd: result.prd, prdMeta: d.meta })
      openContentPanel("prd")
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      showToast("PRD generation failed", msg.slice(0, 200))
    } finally {
      setGeneratingPrd(false)
    }
  }

  if (!d) {
    return (
      <AppLayout mainClassName="main--reading" inlineChat>
        <a className="detail-back" onClick={() => goTo("brief")}>
          ← Weekly brief
        </a>
        <EmptyPane
          title="No evidence loaded"
          hint="Open a finding from this week's brief to view the full evidence."
          placeholders={3}
        />
      </AppLayout>
    )
  }

  return (
    <AppLayout mainClassName="main--reading" inlineChat>
      <a className="detail-back" onClick={() => goTo("brief")}>
        {d.backLabel}
      </a>

      <div className="detail-title-row">
        <div style={{ flex: 1 }}>
          <div className="finding-tag-row" style={{ marginBottom: 10 }}>
            {d.tags.map((t, i) => (
              <span key={i} className={t.className}>
                {t.label}
              </span>
            ))}
          </div>
        </div>
        <button
          type="button"
          className="ask-ai-btn"
          style={{ marginTop: 8 }}
          onClick={() => {
            expandAiPanel()
            setAIBarValue(`About this finding — summarize risks and next steps.`)
          }}
        >
          <AskIcon />
        </button>
      </div>

      {evidence ? (
        evidence.html ? (
          // v3 evidence — self-contained HTML brief (own title/meta); render the
          // sandboxed iframe alone, skipping the panel title/meta/section chrome.
          <div className="prd-frame">
            <div className="prd-body">
              <EvidenceHtmlBrief html={evidence.html} />
            </div>
          </div>
        ) : (
          <div className="prd-frame">
            <div className="prd-body">
              {evidence.metaLine ? (
                <div className="prd-meta">{evidence.metaLine}</div>
              ) : null}
              <h1 className="prd-title">{evidence.title}</h1>
              <EvidenceSections sections={evidence.sections} />
            </div>
          </div>
        )
      ) : evidenceState.kind === "loading" ? (
        <EmptyPane
          title="Generating evidence…"
          hint="Pulling the data-science slicing, infographics, qualitative signals, and hypothesis for this finding."
          placeholders={4}
        />
      ) : evidenceState.kind === "error" ? (
        <EmptyPane
          title="Couldn't load full evidence"
          hint={evidenceState.message}
          placeholders={0}
        />
      ) : null}

      <div className="detail-cta-actions detail-cta-actions-end">
        <button type="button" className="btn" onClick={() => goTo("brief")}>
          Snooze
        </button>
        <button
          type="button"
          className="btn btn-accent"
          onClick={handleGeneratePrd}
          disabled={generatingPrd}
        >
          {generatingPrd ? "Generating PRD…" : "Generate PRD"}
        </button>
      </div>
    </AppLayout>
  )
}

function AskIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
      <path d="M12 8v4M12 15h0" strokeWidth="2.4" />
    </svg>
  )
}
