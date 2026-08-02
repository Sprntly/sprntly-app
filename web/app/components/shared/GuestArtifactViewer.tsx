"use client"

// The guest content-viewing shell. Mounts its OWN fresh NavigationProvider +
// ContentProvider instances — confirmed zero-dependency on
// useWorkspace/useCompany/useAuth (grep, this worktree) — rather than the ones
// (app)/layout.tsx provides, and is NEVER wrapped in WorkspaceProvider /
// CompanyProvider / OnboardingRequiredGuard. This is the fix for both failure
// modes traced in the ticket: OnboardingRequiredGuard's infinite-loading-shell
// for a zero-company guest, and the authed-fetch 403/404 chain
// (ChatScreen/loadPrdById, ContentPanel's evidence/tickets effects) a guest
// can never pass. GuestArtifactViewer reads the shared artifact's content
// exactly once via artifactShareApi.content(token) — or prdAccessApi.content
// (artifactId) for a bare-link, token-less guest (2026-08-02 "full parity"
// scope) — and populates its own ContentContext directly — it never calls
// prdApi.get, loadPrdById, evidenceApi.get, loadEvidenceByInsight, or
// storiesApi.getJob.
import { useEffect, useRef, useState } from "react"
import { NavigationProvider, useNavigation } from "../../context/NavigationContext"
import { ContentProvider, useContent } from "../../context/ContentContext"
import { GuestSessionProvider, type GuestSession } from "../../context/GuestSessionContext"
import { artifactShareApi } from "../../lib/artifactShareApi"
import { prdAccessApi } from "../../lib/prdAccessApi"
import { markdownToPrdState } from "../../lib/prd-adapter"
import { markdownToEvidenceState } from "../../lib/evidence-adapter"
import { conversationsApi, type ConversationTurn } from "../../lib/api"
import { IconLock } from "@tabler/icons-react"
import { GuestRail } from "./GuestRail"
import { ContentPanel } from "./ContentPanel"
import { Toast } from "./Toast"
import { JoinWorkspaceBanner } from "./JoinWorkspaceBanner"
import { JoinConfirmModal } from "./JoinConfirmModal"

export type GuestArtifactViewerProps = {
  /** Null for a bare-link (token-less) guest session — see prdAccessApi. */
  token: string | null
  /** The PRD's real internal id — used for content.prd.prd_id and the
   *  (unrelated, already-int-keyed) conversation-history lookup. NEVER put
   *  in a URL directly; see `publicId` for the external-facing identifier. */
  artifactId: number
  /** The PRD's opaque, unguessable external identifier — what a bare-link
   *  (token-less) session's own content/join API calls key on. Null in
   *  token mode (the share token is the external identifier there). */
  publicId: string | null
  /** Null when there's no share row to attribute a "shared by" to (bare-link
   *  sessions). */
  sharerName: string | null
  owningCompanyName: string
}

function GuestArtifactViewerInner({
  token,
  artifactId,
  publicId,
  sharerName,
  owningCompanyName,
}: GuestArtifactViewerProps) {
  const { setContent } = useContent()
  const { openContentPanel } = useNavigation()
  // Ref (not state) so React 18 dev double-invoke of effects still fetches
  // exactly once per real mount (AC10) — a state-driven guard would still let
  // both invocations start the request before either commits its flip. This
  // ref is also the ONLY guard now: StrictMode's dev-only synthetic
  // mount→cleanup→remount cycle runs cleanup WITHOUT a real unmount, on the
  // SAME instance — a `cancelled` flag set by that synthetic cleanup used to
  // discard the one real in-flight fetch's result permanently (fetchedRef
  // correctly prevented a replacement fetch from ever starting, so the
  // cancelled result was never replaced). Since fetchedRef already guarantees
  // exactly one fetch per real mount, a separate cancelled-on-cleanup check
  // was redundant and actively harmful — removed.
  const fetchedRef = useRef(false)
  const [joinModalOpen, setJoinModalOpen] = useState(false)

  // Read-only chat history for THIS PRD, company-scoped (require_company,
  // not require_workspace) — the one real chat surface an auto-joined
  // company member can actually reach, since sending a new message
  // (backend/app/routes/ask.py) and browsing the general conversation list
  // (GET /v1/conversations) are both require_workspace-gated and stay
  // structurally out of reach until a real workspace join happens. `null`
  // (not an empty array) distinguishes "still loading" from "checked, no
  // conversation exists yet" so the composer's placeholder doesn't flash.
  const [historyTurns, setHistoryTurns] = useState<ConversationTurn[] | null>(null)
  const historyFetchedRef = useRef(false)

  useEffect(() => {
    if (historyFetchedRef.current) return
    historyFetchedRef.current = true
    conversationsApi
      .byPrd(artifactId)
      .then((res) => setHistoryTurns(res.turns))
      .catch(() => setHistoryTurns([])) // best-effort — an empty history reads the same as "none yet"
  }, [artifactId])

  useEffect(() => {
    if (fetchedRef.current) return
    fetchedRef.current = true
    const fetcher = token ? artifactShareApi.content(token) : prdAccessApi.content(publicId!)
    fetcher
      .then((res) => {
        const prdReady = res.prd.status === "ready" && !!res.prd.payload_md
        if (!prdReady) return // best-effort: leave the empty pane up
        const evidenceReady =
          !!res.evidence && res.evidence.status === "ready" && !!res.evidence.payload_md

        setContent({
          prd: {
            ...markdownToPrdState(res.prd.payload_md),
            prd_id: res.prd.id,
            public_id: res.prd.public_id,
            figma_file_key: undefined,
            llmPart: res.prd.llm_part,
            briefId: res.prd.brief_id,
            insightIndex: res.prd.insight_index,
            source: res.prd.source,
            generatedAt: res.prd.generated_at,
            question: res.prd.question,
          },
          prdMeta:
            res.prd.brief_id != null && res.prd.insight_index != null
              ? { briefId: res.prd.brief_id, insightIndex: res.prd.insight_index }
              : null,
          evidence: evidenceReady
            ? { ...markdownToEvidenceState(res.evidence!.payload_md), question: res.evidence!.question }
            : null,
          guestTickets: res.tickets?.stories ?? null,
        })
        openContentPanel("prd")
      })
      .catch(() => {
        // Best-effort — the empty pane stays up rather than an error toast; a
        // guest whose content read fails simply sees "Shared with you".
      })
  }, [token, artifactId, publicId, setContent, openContentPanel])

  return (
    <div className="app">
      <GuestRail />
      <div className="main-column">
        <JoinWorkspaceBanner
          owningCompanyName={owningCompanyName}
          onJoin={() => setJoinModalOpen(true)}
        />
        <main className="main" data-testid="guest-viewer-main">
          {historyTurns && historyTurns.length > 0 ? (
            <div
              className="od-center-inner od-center-inner--home"
              style={{ display: "flex", flexDirection: "column", height: "100%" }}
            >
              <div className="chat-greeting" style={{ paddingBottom: 8 }}>
                <p className="chat-greeting-sub">
                  Past chat about this document, read-only — join the workspace to reply.
                </p>
              </div>
              <div
                data-testid="guest-history-turns"
                style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 12, padding: "8px 0" }}
              >
                {historyTurns.map((turn) => (
                  <div key={turn.id} style={{ maxWidth: 640 }}>
                    <div className="chat-greeting-sub" style={{ fontWeight: 600, marginBottom: 2 }}>
                      {turn.role === "user" ? sharerName ?? "You" : "Sprntly"}
                    </div>
                    <div className="prd-body" style={{ fontSize: 14 }}>
                      {turn.content}
                    </div>
                  </div>
                ))}
              </div>
              <div className="home-landing-composer">
                <div
                  className="chat-home-composer"
                  aria-disabled="true"
                  style={{ opacity: 0.55, cursor: "not-allowed" }}
                >
                  <textarea
                    className="chat-home-composer-input"
                    placeholder="Join the workspace to reply"
                    rows={1}
                    disabled
                    readOnly
                    style={{ cursor: "not-allowed" }}
                  />
                  <div className="chat-home-composer-footer">
                    <div className="chat-home-composer-actions" />
                    <button
                      type="button"
                      className="chat-home-composer-send"
                      aria-label="Chat is locked — join the workspace to send messages"
                      disabled
                    >
                      <IconLock size={14} />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <div className="home-landing-eyeline">
              <div className="od-center-inner od-center-inner--home">
                <div className="chat-greeting">
                  <h1 className="chat-greeting-title">
                    Shared with <em>you</em>.
                  </h1>
                  <p className="chat-greeting-sub">
                    {sharerName
                      ? `${sharerName} shared this from ${owningCompanyName}.`
                      : `Shared from ${owningCompanyName}.`}
                  </p>
                </div>
                <div className="home-landing-composer">
                  <div
                    className="chat-home-composer"
                    aria-disabled="true"
                    style={{ opacity: 0.55, cursor: "not-allowed" }}
                  >
                    <textarea
                      className="chat-home-composer-input"
                      placeholder="Join the workspace to chat about this document"
                      rows={1}
                      disabled
                      readOnly
                      style={{ cursor: "not-allowed" }}
                    />
                    <div className="chat-home-composer-footer">
                      <div className="chat-home-composer-actions" />
                      <button
                        type="button"
                        className="chat-home-composer-send"
                        aria-label="Chat is locked — join the workspace to send messages"
                        disabled
                      >
                        <IconLock size={14} />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
      <ContentPanel />
      <Toast />
      <JoinConfirmModal
        open={joinModalOpen}
        token={token}
        publicId={publicId}
        artifactId={artifactId}
        sharerName={sharerName}
        owningCompanyName={owningCompanyName}
        onClose={() => setJoinModalOpen(false)}
      />
    </div>
  )
}

export function GuestArtifactViewer(props: GuestArtifactViewerProps) {
  const guestSession: GuestSession = {
    token: props.token,
    publicId: props.publicId,
    sharerName: props.sharerName,
    owningCompanyName: props.owningCompanyName,
    artifactId: props.artifactId,
  }
  return (
    <GuestSessionProvider value={guestSession}>
      <NavigationProvider>
        <ContentProvider>
          <GuestArtifactViewerInner {...props} />
        </ContentProvider>
      </NavigationProvider>
    </GuestSessionProvider>
  )
}
