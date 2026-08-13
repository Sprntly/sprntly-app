"use client"

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { useGuestSession } from "../../context/GuestSessionContext"
import { PrdSections } from "./PrdSections"
import { PrdHtmlView, type PrdHtmlHandle } from "./PrdHtmlView"
import { PrdMarkdownEditor, PrdToolbar, type PrdMarkdownHandle } from "./PrdMarkdownEditor"
import { StreamingHtmlPreview, stripLeadingFence } from "./StreamingHtmlPreview"
import { EmptyPane } from "./EmptyPane"
import { GeneratingBanner, GeneratingPane } from "./GenerationState"
import { PRD_GEN } from "./generationPhases"
import { multiAgentApi, prdApi } from "../../lib/api"
import { markdownToPrdState } from "../../lib/prd-adapter"
import { stripHtmlCodeFence } from "../../lib/htmlBrief"
import { mergeHistory, type HistoryEntry } from "../../lib/prdHistory"
import { PrdPatchBanner } from "../design-agent/PrdPatchBanner"
import { IconFileText, IconTicket } from "@tabler/icons-react"
import type { PrdSection, PrdState } from "../../types/content"

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

export function PrdPanelContent({ evidenceTabAvailable = true }: {
  /** Whether the panel's Evidence tab is currently shown. Gates the HTML PRD's
   *  "View more evidence" link (top-3 evidence fold) — with no Evidence tab to
   *  jump to, we render the full list instead of a link that goes nowhere. */
  evidenceTabAvailable?: boolean
} = {}) {
  const { showToast, openContentPanel } = useNavigation()
  const { content, setContent } = useContent()
  const guestSession = useGuestSession()
  const prd = content.prd

  // Jump to the Evidence tab from the HTML PRD's injected "View more evidence"
  // link (stable identity so the iframe's one-time load handler never rebinds).
  const handleViewMoreEvidence = useCallback(() => {
    openContentPanel("evidence")
  }, [openContentPanel])

  // Does this PRD already have persisted tickets? Drives the footer button's
  // "Create tickets" ↔ "View tickets" label (cache-read only, no generation).
  const [hasTickets, setHasTickets] = useState(false)
  useEffect(() => {
    // A guest session's tickets are pre-fetched by GuestArtifactViewer into
    // content.guestTickets — this must never authed-fetch storiesApi.getForPrd
    // for a guest (same class of bug as ContentPanel's guarded effects: it's
    // authed-gated and would 403/404 for an unentitled viewer).
    if (guestSession) {
      setHasTickets((content.guestTickets?.length ?? 0) > 0)
      return
    }
    const prdId = prd?.prd_id
    if (prdId == null) { setHasTickets(false); return }
    let cancelled = false
    void (async () => {
      try {
        const { storiesApi } = await import("../../lib/api")
        const cache = await storiesApi.getForPrd(prdId)
        if (!cancelled) setHasTickets(cache.status === "ready" && cache.stories.length > 0)
      } catch { /* default to "Create tickets" */ }
    })()
    return () => { cancelled = true }
  }, [prd?.prd_id, guestSession, content.guestTickets])

  // NO "load the workspace's latest PRD" fallback lives here any more.
  //
  // It used to fire whenever the panel had no PRD and nothing was generating,
  // fetching prdApi.latest(company) to spare the old standalone PRD screen an
  // empty pane on refresh. That screen is gone — this component is only ever the
  // right rail now, and every caller that opens the PRD tab already supplies
  // either a loaded PRD or `prdGenerating: true`.
  //
  // What it actually did was surface an UNRELATED document. Ask the chat to
  // "generate a PRD for <x>", get the clarify-first questions back, and the rail
  // sat empty and not-generating for exactly as long as it took to answer —
  // whereupon this effect filled it with whatever PRD happened to be newest in
  // the workspace. The user read that as the answer to their request. The same
  // trap was armed after a failed generation, a stopped one, and on some tab
  // switches. A specific PRD is always reachable by id (loadPrdById / the tab's
  // prdId), so "most recent thing in the workspace" was never the right answer.

  // Parsed QA test-scenario sections to render under the PRD. Empty until a
  // ready qa-scenarios doc is fetched and parsed; a failed/absent/not-ready
  // fetch leaves this empty so nothing extra renders. Keyed off the loaded PRD's
  // briefId/insightIndex (carried on PrdState), so EVERY load path triggers it —
  // including the brief card's "View PRD" (loadPrdById), not just latest/openGen.
  const [qaSections, setQaSections] = useState<PrdSection[]>([])

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

  const htmlViewRef = useRef<PrdHtmlHandle>(null)
  // Markdown (non-v3) PRD editor handle — the SAME shared PrdMarkdownEditor the
  // project drawer uses (AD-P13b). The panel drives its manual "Save now"
  // through this imperative handle, mirroring htmlViewRef for the v3 path.
  const mdViewRef = useRef<PrdMarkdownHandle>(null)
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
      setContent({ prd: { ...markdownToPrdState(rec.payload_md), prd_id: rec.id, public_id: rec.public_id, figma_file_key: undefined, llmPart: rec.llm_part, briefId: rec.brief_id, insightIndex: rec.insight_index, source: rec.source } })
      setShowVersions(false)
    } catch {
      showToast("Couldn't open version", "Failed to load that generation.")
    }
  }, [setContent, showToast])

  // The markdown editor's contenteditable body, draft recovery, debounced
  // autosave, and execCommand toolbar now live in the shared PrdMarkdownEditor
  // primitive (AD-P13b) — the panel only drives the manual "Save now" below.

  // Manual save — the bottom "Autosaved" button. The PRD already autosaves on
  // edit (debounced, inside the editor); this lets the user force a save now and
  // is also where the autosave status is surfaced. Both editors expose the same
  // imperative save() handle, so this delegates to whichever is mounted.
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
    // Markdown PRD: the shared editor owns draft + flatten-to-text persistence;
    // its imperative save() re-throws on failure so the toast path is preserved.
    setSaveStatus("saving")
    try {
      await mdViewRef.current?.save()
      setSaveStatus("saved")
      showToast("Saved", "Your PRD has been saved.")
    } catch {
      showToast("Save failed", "Could not save to server. Local draft preserved.")
      setSaveStatus("saved")
    }
  }, [prd, showToast])

  return (
    <div className="cpanel-prd-wrap">
      {/* Scrolling document area — the footer action bar below stays PINNED
          to the panel's bottom edge (mirrors how the header holds the tabs). */}
      <div className="prd-scroll">
      {prd && <PrdPatchBanner prdId={prd.prd_id} />}

      <div className="prd-frame">
        {/* The disabled no-document toolbar shown in the empty / generating /
            streaming states (there's no PRD to edit yet). Once a markdown PRD is
            loaded its toolbar comes from the shared PrdMarkdownEditor below; a
            v3 HTML PRD is edited natively in the iframe (no execCommand toolbar);
            and a guest never sees an edit control (AC15). */}
        {!prd && !guestSession && <PrdToolbar hasDoc={false} saveStatus={saveStatus} exec={() => {}} />}
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
              onViewMoreEvidence={evidenceTabAvailable ? handleViewMoreEvidence : undefined}
              // The v3 HTML PRD's contenteditable + autosave-on-input loop is a
              // real write path independent of this panel's own Save/toolbar
              // guards — PrdHtmlView itself must refuse to persist for a guest.
              readOnly={!!guestSession}
            />
            {qaSections.length > 0 && (
              <div className="prd-qa-scenarios" data-testid="prd-qa-scenarios">
                <h2 className="prd-h2">Test Scenarios</h2>
                <PrdSections sections={qaSections} />
              </div>
            )}
          </>
        ) : prd ? (
          // AD-P13b — one editor, two consumers: the markdown PRD's
          // contenteditable body + execCommand toolbar + draft/autosave now
          // live in the shared PrdMarkdownEditor. onSave is OMITTED here so the
          // main-chat save path (prdApi.update) is byte-for-byte unchanged; the
          // project drawer injects a gated save into the SAME primitive.
          <PrdMarkdownEditor
            ref={mdViewRef}
            prdId={prd.prd_id}
            title={prd.title}
            onStatus={setSaveStatus}
            readOnly={!!guestSession}
            beforeBody={<PrdSummaryStrip prd={prd} />}
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
          </PrdMarkdownEditor>
        ) : content.prdGenerating && content.prdPartialHtml ? (
          // Live streaming preview: partial Part A HTML is already arriving —
          // render it as it grows, with a slim pulsing indicator instead of the
          // full-pane spinner. The finished PRD (poll result) replaces this.
          <div className="prd-body" style={{ minHeight: 280 }}>
            <GeneratingBanner
              testId="prd-streaming"
              title="Writing the PRD…"
              sub="Rendering it below as it's written — the finished draft replaces this."
            />
            <StreamingHtmlPreview
              html={stripLeadingFence(stripHtmlCodeFence(content.prdPartialHtml))}
              title="PRD draft (generating)"
              testId="prd-streaming-preview"
            />
          </div>
        ) : (
          <div className="prd-body" style={{ minHeight: 280 }}>
            {content.prdGenerating ? (
              <GeneratingPane
                {...PRD_GEN}
                testId="prd-generating"
                icon={<IconFileText size={19} />}
                title="Generating PRD…"
              />
            ) : (
              <EmptyPane title="No PRD draft loaded" hint="Generate a PRD from the Top Insights by selecting an insight and clicking Generate PRD." placeholders={0} />
            )}
          </div>
        )}

      </div>
      </div>

      {/* Version history dropdown — expands ABOVE the pinned footer bar
          (its list has its own internal scroll cap). */}
      {showVersions && prd && (
        <div style={{ margin: "0 24px 12px", borderRadius: 10, border: "1px solid var(--line)", background: "var(--surface)", overflow: "hidden", flexShrink: 0 }}>
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

      {/* Pinned footer action bar — fixed to the panel's bottom edge (the way
          the header holds the tabs): autosave status, Version history toggle,
          and the prototype CTA. */}
      {prd && (
        <div className="prd-bottom-bar prd-footer-bar">
          {/* Autosave/version-history are edit affordances — withheld entirely
              in guest mode (AC15), not merely disabled, since neither reflects
              anything meaningful for a document a guest can't edit. */}
          {!guestSession && (
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            disabled={saveStatus === "saving"}
            onClick={saveNow}
            title="This PRD autosaves as you edit — click to save now"
          >
            {saveStatus === "saving" ? "Saving…" : saveStatus === "unsaved" ? "Save now" : "✓ Autosaved"}
          </button>
          )}
          {!guestSession && (
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
          )}

          {/* Next pipeline step from the PRD is TICKETS. Reads "Create tickets"
              until the PRD has been broken into stories, then "View tickets";
              either way it just opens the Tickets tab (which generates on first
              open). The prototype affordance moved to the Tickets tab's bar. */}
          <div style={{ marginLeft: "auto" }}>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              data-testid="prd-footer-tickets-cta"
              onClick={() => openContentPanel("tickets")}
              style={{ display: "inline-flex", alignItems: "center", gap: 6, borderRadius: 999 }}
            >
              <IconTicket size={13} />
              {hasTickets ? "View tickets" : "Create tickets"}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
