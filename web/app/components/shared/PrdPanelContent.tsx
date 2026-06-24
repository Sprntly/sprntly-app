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
import { ArtifactFooterActions } from "./ArtifactFooterActions"
import { DesignAgentLauncher } from "../design-agent/DesignAgentLauncher"
import { EmptyPane } from "./EmptyPane"
import { ApiError, designAgentApi, multiAgentApi, prdApi, type PrototypeRecord } from "../../lib/api"
import { markdownToPrdState } from "../../lib/prd-adapter"
import { mergeHistory, type HistoryEntry } from "../../lib/prdHistory"
import { runDesignAgentGeneration } from "../../lib/runDesignAgentGeneration"
import { PrdPatchBanner } from "../design-agent/PrdPatchBanner"
import {
  IconCheck,
  IconGrid,
  IconLinkInsert,
  IconListBullet,
  IconRedo,
  IconUndo,
} from "./app-icons"
import type { PrdSection, PrdState } from "../../types/content"

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

function PrototypeSection({ prdId, figmaFileKey, externalGeneratingId }: { prdId: number; figmaFileKey?: string | null; externalGeneratingId?: number | null }) {
  const [existing, setExisting] = useState<PrototypeRecord | null>(null)
  const [loading, setLoading] = useState(true)
  const [polling, setPolling] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setExisting(null)
    designAgentApi.getByPrd(prdId).then((proto) => {
      if (cancelled) return
      if (proto && proto.status === "ready") { setExisting(proto); setLoading(false) }
      else if (proto && proto.status === "generating") {
        setPolling(true); setLoading(false)
        runDesignAgentGeneration({ prototypeId: proto.id }).then((result) => {
          if (cancelled) return
          setPolling(false)
          if (result.ok) setExisting(result.prototype)
        })
      } else { setLoading(false) }
    })
    return () => { cancelled = true }
  }, [prdId])

  if (loading) return null
  return (
    <div style={{ marginTop: 24 }}>
      {polling && !existing && (
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 14px", borderRadius: 10, border: "1px solid var(--accent-alpha-14)", background: "var(--accent-muted)" }}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden style={{ flexShrink: 0, animation: "da-spin 0.9s linear infinite" }}>
            <circle cx="8" cy="8" r="6" stroke="var(--accent-alpha-28)" strokeWidth="2" />
            <path d="M8 2a6 6 0 0 1 6 6" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" />
          </svg>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--accent-ink)" }}>Generating prototype…</div>
            <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 3 }}>This usually takes 1–2 minutes.</div>
          </div>
        </div>
      )}
      <DesignAgentLauncher prdId={prdId} figmaFileKey={figmaFileKey} externalGeneratingId={externalGeneratingId} />
    </div>
  )
}

// ── LLM-readable view ─────────────────────────────────────────────────────
export function LlmReadableView({ prd, generating, loading }: { prd: PrdState | null; generating?: boolean; loading?: boolean }) {
  const { showToast } = useNavigation()

  if (!prd) {
    return (
      <div className="llm-view-empty">
        {generating ? (
          <p data-testid="llm-generating"><span className="prd-loader" aria-hidden /> Generating PRD… the implementation brief will appear here.</p>
        ) : loading ? (
          <p>Loading PRD…</p>
        ) : (
          <p>No PRD yet — generate one and the implementation brief will appear here.</p>
        )}
      </div>
    )
  }

  // Render the REAL Part B (implementation-spec markdown) the backend stores in
  // `llm_part` — EARS requirements, design/contracts, dependency-ordered tasks,
  // acceptance tests, Definition of Done, verification report. No reconstruction
  // from Part A: parse the markdown and render it faithfully via PrdSections.
  const llmPart = prd.llmPart?.trim() ?? ""
  const partB = llmPart ? markdownToPrdState(llmPart) : null

  return (
    <div className="llm-view">
      <div className="llm-view-header">
        <span className="llm-view-label">LLM-READABLE · FOR AGENT IMPLEMENTATION</span>
      </div>

      <h2 className="llm-view-title">Implementation brief</h2>
      <p className="llm-view-subtitle">
        Plain-English, structured so an AI agent can implement, test, and verify against a clear definition of done.
      </p>

      <div className="llm-view-actions">
        <button
          type="button"
          className="llm-action-btn"
          disabled={!llmPart}
          onClick={() => {
            if (!llmPart) return
            navigator.clipboard.writeText(llmPart).catch(() => {})
            showToast("Copied", "Implementation brief copied to clipboard.")
          }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </svg>
          Copy brief
        </button>
        <button
          type="button"
          className="llm-action-btn llm-action-btn--accent"
          onClick={() => showToast("Sent to Claude Code", "The implementation brief has been sent to Claude Code.")}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" />
          </svg>
          Send to Claude Code
        </button>
      </div>

      {partB ? (
        <div className="llm-section" data-testid="llm-part-b">
          <PrdSections sections={partB.sections} />
        </div>
      ) : (
        <div className="llm-section">
          <p className="llm-section-body" style={{ color: "var(--ink-2)" }} data-testid="llm-part-b-empty">
            No implementation spec yet — it generates alongside the PRD.
          </p>
        </div>
      )}
    </div>
  )
}

type PrdSubTab = "human" | "llm"

export function PrdPanelContent() {
  const { openModal, showToast } = useNavigation()
  const { content, setContent } = useContent()
  const { activeCompany } = useCompany()
  const prd = content.prd
  const [subTab, setSubTab] = useState<PrdSubTab>("human")

  // Tracks an in-flight prototype id when "Notify me when ready" was clicked in
  // the loading overlay — surfaces PrototypeGeneratingCard on the PRD without
  // requiring PrototypeSection to remount.
  const [notifyGenId, setNotifyGenId] = useState<number | null>(null)

  const [prdLoading, setPrdLoading] = useState(false)

  // The brief insight the loaded PRD was generated from (PrdState only carries
  // prd_id, but the PrdRecord wire shape also has brief_id/insight_index). Kept
  // here so we can fetch the matching QA test-scenarios doc for the same source.
  const [briefRef, setBriefRef] = useState<{ briefId: number; insightIndex: number } | null>(null)
  // Parsed QA test-scenario sections to render under the PRD. Empty until a
  // ready qa-scenarios doc is fetched and parsed; a failed/absent/not-ready
  // fetch leaves this empty so nothing extra renders.
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
      setBriefRef({ briefId: record.brief_id, insightIndex: record.insight_index })
      setContent({ prd: { ...markdownToPrdState(record.payload_md), prd_id: record.id, figma_file_key: undefined, llmPart: record.llm_part } })
    }).catch((e) => {
      if (e instanceof ApiError && e.status === 404) return
    }).finally(() => { if (!cancelled) setPrdLoading(false) })
    return () => { cancelled = true }
  }, [prd, activeCompany, content.prdGenerating, setContent])

  // After the PRD's brief reference is known, ALSO fetch the QA test-scenarios
  // doc for the same brief_id + insight_index. Render its parsed sections only
  // when the doc is present AND ready; otherwise render nothing extra. Resilient:
  // a failed/absent fetch never breaks the PRD view (errors swallowed → empty).
  useEffect(() => {
    if (!briefRef) { setQaSections([]); return }
    let cancelled = false
    multiAgentApi
      .getQaScenarios(briefRef.briefId, briefRef.insightIndex)
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
  }, [briefRef])

  const bodyRef = useRef<HTMLDivElement>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [saveStatus, setSaveStatus] = useState<SaveStatus>("saved")
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [showVersions, setShowVersions] = useState(false)
  const [versionsLoading, setVersionsLoading] = useState(false)

  // Open a prior generation (a different prds row) into the panel.
  const openGeneration = useCallback(async (genId: number) => {
    try {
      const rec = await prdApi.get(genId)
      setBriefRef({ briefId: rec.brief_id, insightIndex: rec.insight_index })
      setContent({ prd: { ...markdownToPrdState(rec.payload_md), prd_id: rec.id, figma_file_key: undefined, llmPart: rec.llm_part } })
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

  useEffect(() => {
    const onGenerating = (e: Event) => {
      const id = (e as CustomEvent<{ prototypeId: number }>).detail?.prototypeId
      if (typeof id === "number") setNotifyGenId(id)
    }
    const onDone = () => setNotifyGenId(null)
    window.addEventListener("da:generating", onGenerating)
    window.addEventListener("da:generating-done", onDone)
    return () => {
      window.removeEventListener("da:generating", onGenerating)
      window.removeEventListener("da:generating-done", onDone)
    }
  }, [])

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

  return (
    <div className="cpanel-prd-wrap">
      {/* Sub-tabs: Human-readable / LLM-readable */}
      <div className="prd-subtab-bar">
        <div className="prd-subtabs">
          <button
            type="button"
            className={`prd-subtab${subTab === "human" ? " prd-subtab--active" : ""}`}
            onClick={() => setSubTab("human")}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <circle cx="12" cy="8" r="4" /><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7" />
            </svg>
            Human-readable
          </button>
          <button
            type="button"
            className={`prd-subtab${subTab === "llm" ? " prd-subtab--active" : ""}`}
            onClick={() => setSubTab("llm")}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <rect x="3" y="3" width="18" height="18" rx="3" /><path d="M8 12h8M8 8h5M8 16h3" />
            </svg>
            LLM-readable
          </button>
        </div>
        {subTab === "llm" && (
          <button
            type="button"
            className="prd-send-claude-btn"
            onClick={() => showToast("Sent to Claude Code", "The implementation brief has been sent to Claude Code.")}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" />
            </svg>
            Send to Claude Code
          </button>
        )}
      </div>

      {subTab === "llm" ? (
        <LlmReadableView prd={prd} generating={content.prdGenerating} loading={prdLoading} />
      ) : (
      <>
      {prd && <PrdPatchBanner prdId={prd.prd_id} />}

      <div className="prd-frame">
        <PrdToolbar hasDoc={!!prd} saveStatus={saveStatus} exec={exec} />
        {prd ? (
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

        <div className="prd-foot">
          <div className="prd-foot-left">
            <button type="button" className="btn btn-ghost btn-sm" disabled={!prd} onClick={async () => {
              if (!prd || !bodyRef.current) return
              setSaveStatus("saving")
              try {
                await prdApi.update(prd.prd_id, { title: prd.title, payload_md: bodyRef.current.innerText || "" })
                setSaveStatus("saved")
                showToast("Draft saved", "Your PRD has been saved.")
              } catch { showToast("Save failed", "Could not save to server. Local draft preserved."); setSaveStatus("saved") }
            }}>
              Save as draft
            </button>
            <button type="button" className="btn btn-ghost btn-sm" disabled={!prd} onClick={async () => {
              if (!prd) return
              setShowVersions(!showVersions)
              if (!showVersions) {
                setVersionsLoading(true)
                try {
                  const [v, g] = await Promise.all([prdApi.listVersions(prd.prd_id), prdApi.listGenerations(prd.prd_id)])
                  setHistory(mergeHistory(v, g, prd.prd_id))
                } catch { setHistory([]) }
                setVersionsLoading(false)
              }
            }} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
              Version history
              <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor" style={{ transform: showVersions ? "rotate(180deg)" : "none", transition: "transform 0.2s" }}>
                <path d="M5 7L1 3h8z" />
              </svg>
            </button>
          </div>
          <div className="prd-foot-right">
            <button type="button" className="btn btn-accent" disabled={!prd} onClick={() => prd && openModal("approve")}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <IconCheck size={16} /> Approve & next step
              </span>
            </button>
          </div>
        </div>
      </div>

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

      {prd && <PrototypeSection prdId={prd.prd_id} figmaFileKey={prd.figma_file_key ?? null} externalGeneratingId={notifyGenId} />}

      {prd && <ArtifactFooterActions current="prd" />}
      </>
      )}
    </div>
  )
}

