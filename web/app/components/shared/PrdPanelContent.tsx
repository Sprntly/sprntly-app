"use client"

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useCompany } from "../../context/CompanyContext"
import { PrdSections } from "./PrdSections"
import { PrdHtmlView, type PrdHtmlHandle } from "./PrdHtmlView"
import { SendToClaudeCode } from "./SendToClaudeCode"
import { GeneratePrototypeCTA } from "../design-agent/GeneratePrototypeCTA"
import { EmptyPane } from "./EmptyPane"
import { ApiError, multiAgentApi, prdApi } from "../../lib/api"
import { markdownToPrdState } from "../../lib/prd-adapter"
import { mergeHistory, type HistoryEntry } from "../../lib/prdHistory"
import { PrdPatchBanner } from "../design-agent/PrdPatchBanner"
import {
  IconGrid,
  IconLinkInsert,
  IconListBullet,
  IconRedo,
  IconUndo,
} from "./app-icons"
import type { PrdSection, PrdState } from "../../types/content"
import footerStyles from "./design-agent-prd-footer.module.css"

const PRD_DRAFT_KEY = (prdId: number) => `sprntly_prd_draft_${prdId}`
function loadDraft(prdId: number): string | null {
  try { return localStorage.getItem(PRD_DRAFT_KEY(prdId)) } catch { return null }
}
function saveDraft(prdId: number, html: string) {
  try { localStorage.setItem(PRD_DRAFT_KEY(prdId), html) } catch { /* ignore */ }
}

type SaveStatus = "saved" | "saving" | "unsaved"

function PrdSummaryStrip({ prd }: { prd: PrdState }) {
  const tldr = prd.sections.find((s) => s.type === "prd-tldr")
  if (!tldr || tldr.type !== "prd-tldr") return null
  return (
    <div style={{ display: "flex", gap: 0, marginBottom: 20, borderRadius: 10, border: "1px solid var(--line)", overflow: "hidden", fontSize: 12.5 }}>
      {[
        { label: "Problem", text: tldr.problem, accent: "var(--danger-soft)", ink: "var(--danger)" },
        { label: "Fix", text: tldr.fix, accent: "var(--accent-muted)", ink: "var(--accent-ink)" },
        { label: "Impact", text: tldr.impact, accent: "var(--surface-2)", ink: "var(--ink-2)" },
      ].map(({ label, text, accent, ink }, i, arr) => (
        <div key={label} style={{ flex: 1, padding: "10px 14px", background: accent, borderRight: i < arr.length - 1 ? "1px solid var(--line)" : undefined }}>
          <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: ink, marginBottom: 4 }}>{label}</div>
          <div style={{ color: "var(--ink)", lineHeight: 1.45 }}>{text}</div>
        </div>
      ))}
    </div>
  )
}

function PrdToolbar({ hasDoc, saveStatus, exec }: { hasDoc: boolean; saveStatus: SaveStatus; exec: (cmd: string, value?: string) => void }) {
  const statusLabel = saveStatus === "saving" ? "Saving…" : saveStatus === "unsaved" ? "Unsaved" : "Saved · Draft"
  const statusColor = saveStatus === "saving" ? "var(--accent)" : saveStatus === "unsaved" ? "var(--ink-3)" : "var(--accent)"
  return (
    <div className="prd-toolbar">
      <div className="prd-tools-l">
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Undo" onClick={() => exec("undo")}><IconUndo size={16} /></button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Redo" onClick={() => exec("redo")}><IconRedo size={16} /></button>
        <div className="prd-tool-divider" />
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Bold" onClick={() => exec("bold")}><strong>B</strong></button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Italic" onClick={() => exec("italic")}><em>I</em></button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Underline" onClick={() => exec("underline")}><u>U</u></button>
        <div className="prd-tool-divider" />
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Heading 1" onClick={() => exec("formatBlock", "h1")}>H1</button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Heading 2" onClick={() => exec("formatBlock", "h2")}>H2</button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Bullet list" onClick={() => exec("insertUnorderedList")}><IconListBullet size={16} /></button>
        <div className="prd-tool-divider" />
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Insert link" style={{ display: "inline-flex", alignItems: "center" }} onClick={() => { const url = prompt("Enter URL"); if (url) exec("createLink", url) }}>
          <IconLinkInsert size={15} /><span style={{ marginLeft: 5 }}>Link</span>
        </button>
        <button type="button" className="prd-tool" disabled={!hasDoc} title="Insert table" style={{ display: "inline-flex", alignItems: "center" }}>
          <IconGrid size={15} /><span style={{ marginLeft: 5 }}>Table</span>
        </button>
      </div>
      <div className="prd-status">
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: hasDoc ? statusColor : "var(--muted)", transition: "background 0.3s" }} />
        {hasDoc ? statusLabel : "No draft"}
      </div>
    </div>
  )
}

function ViewPrototypeButton({ prdId, figmaFileKey }: { prdId: number; figmaFileKey?: string | null }) {
  return (
    <GeneratePrototypeCTA
      prdId={prdId}
      figmaFileKey={figmaFileKey}
      render={({ label, onClick, disabled }) => (
        <button type="button" className="prd-send-claude-btn" disabled={disabled} onClick={onClick}>
          {label}
        </button>
      )}
    />
  )
}

export function PrdPanelContent() {
  const { showToast } = useNavigation()
  const { content, setContent } = useContent()
  const { activeCompany } = useCompany()
  const prd = content.prd

  const [prdLoading, setPrdLoading] = useState(false)

  // Parsed QA test-scenario sections to render under the PRD. Empty until a
  // ready qa-scenarios doc is fetched and parsed; a failed/absent/not-ready
  // fetch leaves this empty so nothing extra renders. Keyed off the loaded PRD's
  // briefId/insightIndex (carried on PrdState), so EVERY load path triggers it —
  // including the brief card's "View PRD" (loadPrdById), not just latest/openGen.
  const [qaSections, setQaSections] = useState<PrdSection[]>([])

  useEffect(() => {
    // Skip the "load latest PRD" fetch while a generation is actively in flight —
    // the in-progress flow will populate `content.prd` itself, and we don't want
    // to race it with a stale latest record.
    if (prd || !activeCompany || content.prdGenerating) return
    let cancelled = false
    setPrdLoading(true)
    prdApi.latest(activeCompany).then((record) => {
      if (cancelled || !record.payload_md) return
      setContent({ prd: { ...markdownToPrdState(record.payload_md), prd_id: record.id, figma_file_key: undefined, llmPart: record.llm_part, briefId: record.brief_id, insightIndex: record.insight_index } })
    }).catch((e) => {
      if (e instanceof ApiError && e.status === 404) return
    }).finally(() => { if (!cancelled) setPrdLoading(false) })
    return () => { cancelled = true }
  }, [prd, activeCompany, content.prdGenerating, setContent])

  // After the PRD's brief reference is known, ALSO fetch the QA test-scenarios
  // doc for the same brief_id + insight_index. Render its parsed sections only
  // when the doc is present AND ready; otherwise render nothing extra. Resilient:
  // a failed/absent fetch never breaks the PRD view (errors swallowed → empty).
  const qaBriefId = prd?.briefId
  const qaInsightIndex = prd?.insightIndex
  useEffect(() => {
    if (qaBriefId == null || qaInsightIndex == null) { setQaSections([]); return }
    let cancelled = false
    multiAgentApi
      .getQaScenarios(qaBriefId, qaInsightIndex)
      .then((res) => {
        if (cancelled) return
        const doc = res.doc
        if (!doc || doc.status !== "ready" || !doc.payload_md) {
          setQaSections([])
          return
        }
        // markdownToPrdState yields the qa-scenarios section among any
        // title/strategy paragraphs in the QA doc's payload.
        setQaSections(markdownToPrdState(doc.payload_md).sections)
      })
      .catch(() => { if (!cancelled) setQaSections([]) })
    return () => { cancelled = true }
  }, [qaBriefId, qaInsightIndex])

  const bodyRef = useRef<HTMLDivElement>(null)
  const htmlViewRef = useRef<PrdHtmlHandle>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("saved")
  // v3 PRDs are a self-contained HTML page (prd-author v4.2), rendered + edited
  // in a sandboxed iframe (PrdHtmlView) rather than the markdown section editor.
  const isHtmlPrd = !!prd?.html
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [showVersions, setShowVersions] = useState(false)
  const [versionsLoading, setVersionsLoading] = useState(false)

  // Open a prior generation (a different prds row) into the panel.
  const openGeneration = useCallback(async (genId: number) => {
    try {
      const rec = await prdApi.get(genId)
      setContent({ prd: { ...markdownToPrdState(rec.payload_md), prd_id: rec.id, figma_file_key: undefined, llmPart: rec.llm_part, briefId: rec.brief_id, insightIndex: rec.insight_index } })
      setShowVersions(false)
    } catch {
      showToast("Couldn't open version", "Failed to load that generation.")
    }
  }, [setContent, showToast])

  useEffect(() => {
    if (!prd || !bodyRef.current) return
    const draft = loadDraft(prd.prd_id)
    if (draft) bodyRef.current.innerHTML = draft
  }, [prd?.prd_id])

  const handleInput = useCallback(() => {
    setSaveStatus("unsaved")
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(async () => {
      if (!prd || !bodyRef.current) return
      setSaveStatus("saving")
      const html = bodyRef.current.innerHTML
      saveDraft(prd.prd_id, html)
      const textContent = bodyRef.current.innerText || ""
      try {
        await prdApi.update(prd.prd_id, { title: prd.title, payload_md: textContent })
        setSaveStatus("saved")
      } catch { setSaveStatus("saved") }
    }, 2000)
  }, [prd])

  const exec = (cmd: string, value?: string) => {
    bodyRef.current?.focus()
    document.execCommand(cmd, false, value)
  }

  // Manual save — the bottom "Autosaved" button. The PRD already autosaves on
  // edit (handleInput, debounced); this lets the user force a save now and is
  // also where the autosave status is surfaced.
  const saveNow = useCallback(async () => {
    if (!prd) return
    // v3 HTML PRD: the iframe view owns persistence (round-trips the full HTML
    // document, not flattened text) — delegate the manual save to it.
    if (prd.html) {
      setSaveStatus("saving")
      try {
        await htmlViewRef.current?.save()
        setSaveStatus("saved")
        showToast("Saved", "Your PRD has been saved.")
      } catch {
        showToast("Save failed", "Could not save to server. Local draft preserved.")
        setSaveStatus("saved")
      }
      return
    }
    if (!bodyRef.current) return
    setSaveStatus("saving")
    saveDraft(prd.prd_id, bodyRef.current.innerHTML)
    try {
      await prdApi.update(prd.prd_id, { title: prd.title, payload_md: bodyRef.current.innerText || "" })
      setSaveStatus("saved")
      showToast("Saved", "Your PRD has been saved.")
    } catch {
      showToast("Save failed", "Could not save to server. Local draft preserved.")
      setSaveStatus("saved")
    }
  }, [prd, showToast])

  return (
    <div className="cpanel-prd-wrap">
      {prd && <PrdPatchBanner prdId={prd.prd_id} />}

      <div className="prd-frame">
        {/* The markdown editor toolbar (execCommand) doesn't apply to the v3
            HTML page — it's edited natively inside the iframe — so hide it. */}
        {!isHtmlPrd && <PrdToolbar hasDoc={!!prd} saveStatus={saveStatus} exec={exec} />}
        {prd && isHtmlPrd ? (
          <>
            {/* Key on the HTML so a scoped edit (e.g. answering a "User input
                needed" question — same prd_id, new document) forces a remount:
                PrdHtmlView resolves its initial doc once per key, so without this
                a same-prd HTML change would not re-render inside the iframe. */}
            <PrdHtmlView
              key={`${prd.prd_id}:${prd.html?.length ?? 0}`}
              ref={htmlViewRef}
              html={prd.html ?? ""}
              prdId={prd.prd_id}
              title={prd.title}
              onStatus={setSaveStatus}
            />
            {qaSections.length > 0 && (
              <div className="prd-qa-scenarios" data-testid="prd-qa-scenarios">
                <h2 className="prd-h2">Test Scenarios</h2>
                <PrdSections sections={qaSections} />
              </div>
            )}
          </>
        ) : prd ? (
          <>
            <PrdSummaryStrip prd={prd} />
            <div
              className="prd-body"
              contentEditable
              spellCheck={false}
              suppressContentEditableWarning
              ref={bodyRef}
              onInput={handleInput}
            >
              <div className="prd-meta">{prd.metaLine}</div>
              <h1 className="prd-title">{prd.title}</h1>
              <PrdSections sections={prd.sections} prdId={prd.prd_id} figmaFileKey={prd.figma_file_key ?? null} prdTitle={prd.title} />
              {qaSections.length > 0 && (
                <div className="prd-qa-scenarios" data-testid="prd-qa-scenarios">
                  <h2 className="prd-h2">Test Scenarios</h2>
                  <PrdSections sections={qaSections} />
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="prd-body" style={{ minHeight: 280 }}>
            {content.prdGenerating ? (
              <div data-testid="prd-generating" style={{ display: "flex", alignItems: "center", gap: 12, padding: 32, color: "var(--ink-2)" }}>
                <span className="prd-loader" aria-hidden /> Generating PRD…
              </div>
            ) : prdLoading ? (
              <div style={{ display: "flex", alignItems: "center", gap: 12, padding: 32, color: "var(--ink-2)" }}>
                <span className="prd-loader" aria-hidden /> Loading PRD…
              </div>
            ) : (
              <EmptyPane title="No PRD draft loaded" hint="Generate a PRD from the Weekly Brief by selecting an insight and clicking Generate PRD." placeholders={0} />
            )}
          </div>
        )}

      </div>

      {/* Bottom action row: autosave status (click = save now) + Version history
          toggle. Replaces the old mid-page footer; version history lives here at
          the very bottom and expands the panel below. */}
      {prd && (
        <div className="prd-bottom-bar" style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 16 }}>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={saveStatus === "saving"}
            onClick={saveNow}
            title="This PRD autosaves as you edit — click to save now"
          >
            {saveStatus === "saving" ? "Saving…" : saveStatus === "unsaved" ? "Save now" : "✓ Autosaved"}
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={async () => {
              setShowVersions(!showVersions)
              if (!showVersions) {
                setVersionsLoading(true)
                try {
                  const [v, g] = await Promise.all([prdApi.listVersions(prd.prd_id), prdApi.listGenerations(prd.prd_id)])
                  setHistory(mergeHistory(v, g, prd.prd_id))
                } catch { setHistory([]) }
                setVersionsLoading(false)
              }
            }}
            style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
          >
            Version history
            <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor" style={{ transform: showVersions ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}>
              <path d="M5 7L1 3h8z" />
            </svg>
          </button>
          {/* Hand the PRD off to a coding agent: generate (and cache) the
              machine-readable Implementation Spec on demand and copy it to the
              clipboard. The machine PRD is no longer a viewable tab. */}
          <div className={`prd-bottom-actions ${footerStyles.actions}`}>
            <ViewPrototypeButton prdId={prd.prd_id} figmaFileKey={prd.figma_file_key ?? null} />
            <SendToClaudeCode prdId={prd.prd_id} onToast={showToast} />
          </div>
        </div>
      )}

      {showVersions && prd && (
        <div style={{ marginTop: 12, borderRadius: 10, border: "1px solid var(--line)", background: "var(--surface)", overflow: "hidden" }}>
          <div style={{ padding: "10px 16px", background: "var(--surface-2)", borderBottom: "1px solid var(--line)", fontSize: 12, fontWeight: 600, color: "var(--ink-2)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <span>Version History</span>
            <span style={{ fontSize: 11, fontWeight: 400, color: "var(--ink-4)" }}>{history.length} version{history.length !== 1 ? "s" : ""}</span>
          </div>
          {versionsLoading ? (
            <div style={{ padding: "20px 16px", textAlign: "center", fontSize: 12, color: "var(--ink-4)" }}>Loading versions...</div>
          ) : history.length === 0 ? (
            <div style={{ padding: "20px 16px", textAlign: "center", fontSize: 12, color: "var(--ink-4)" }}>No versions saved yet.</div>
          ) : (
            <div style={{ maxHeight: 260, overflowY: "auto" }}>
              {history.map((e) => {
                const rowStyle = { display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 16px", borderBottom: "1px solid var(--line)", fontSize: 12.5 } as const
                const actionStyle = { fontSize: 11, padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--surface)", cursor: "pointer", color: "var(--accent)", fontWeight: 600 } as const
                if (e.kind === "snapshot") {
                  const v = e.snapshot
                  return (
                    <div key={`s${v.id}`} style={rowStyle}>
                      <div>
                        <div style={{ fontWeight: 500, color: "var(--ink)" }}>v{v.version_number} — {v.title.slice(0, 50)}</div>
                        <div style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 2 }}>Edit · {v.saved_by} · {new Date(v.saved_at).toLocaleString()}</div>
                      </div>
                      <button type="button" onClick={async () => {
                        try { await prdApi.restoreVersion(prd.prd_id, v.id); showToast("Version restored", `Restored to v${v.version_number}.`); window.location.reload() }
                        catch { showToast("Restore failed", "Could not restore this version.") }
                      }} style={actionStyle}>
                        Restore
                      </button>
                    </div>
                  )
                }
                const g = e.generation
                return (
                  <div key={`g${g.id}`} style={rowStyle}>
                    <div>
                      <div style={{ fontWeight: 500, color: "var(--ink)" }}>{g.title.slice(0, 50)}</div>
                      <div style={{ fontSize: 11, color: "var(--ink-4)", marginTop: 2 }}>Generated · {new Date(g.generated_at).toLocaleString()}</div>
                    </div>
                    {e.isCurrent
                      ? <span style={{ fontSize: 11, color: "var(--ink-4)", fontWeight: 600 }}>Current</span>
                      : <button type="button" onClick={() => openGeneration(g.id)} style={actionStyle}>Open</button>}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
