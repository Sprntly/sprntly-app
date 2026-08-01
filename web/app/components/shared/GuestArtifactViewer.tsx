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
// exactly once via artifactShareApi.content(token) and populates its own
// ContentContext directly — it never calls prdApi.get, loadPrdById,
// evidenceApi.get, loadEvidenceByInsight, or storiesApi.getJob.
import { useEffect, useRef } from "react"
import { NavigationProvider, useNavigation } from "../../context/NavigationContext"
import { ContentProvider, useContent } from "../../context/ContentContext"
import { GuestSessionProvider, type GuestSession } from "../../context/GuestSessionContext"
import { artifactShareApi } from "../../lib/artifactShareApi"
import { markdownToPrdState } from "../../lib/prd-adapter"
import { markdownToEvidenceState } from "../../lib/evidence-adapter"
import { GuestRail } from "./GuestRail"
import { EmptyPane } from "./EmptyPane"
import { ContentPanel } from "./ContentPanel"
import { Toast } from "./Toast"

export type GuestArtifactViewerProps = {
  token: string
  artifactId: number
  sharerName: string
  owningCompanyName: string
}

function GuestArtifactViewerInner({ token, sharerName, owningCompanyName }: GuestArtifactViewerProps) {
  const { setContent } = useContent()
  const { openContentPanel } = useNavigation()
  // Ref (not state) so React 18 dev double-invoke of effects still fetches
  // exactly once per real mount (AC10) — a state-driven guard would still let
  // both invocations start the request before either commits its flip.
  const fetchedRef = useRef(false)

  useEffect(() => {
    if (fetchedRef.current) return
    fetchedRef.current = true
    let cancelled = false
    artifactShareApi
      .content(token)
      .then((res) => {
        if (cancelled) return
        const prdReady = res.prd.status === "ready" && !!res.prd.payload_md
        if (!prdReady) return // best-effort: leave the empty pane up
        const evidenceReady =
          !!res.evidence && res.evidence.status === "ready" && !!res.evidence.payload_md

        setContent({
          prd: {
            ...markdownToPrdState(res.prd.payload_md),
            prd_id: res.prd.id,
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
    return () => {
      cancelled = true
    }
  }, [token, setContent, openContentPanel])

  return (
    <div className="app">
      <GuestRail />
      <div className="main-column">
        <main className="main" data-testid="guest-viewer-main">
          <EmptyPane
            title="Shared with you"
            hint={`${sharerName} shared this from ${owningCompanyName}.`}
          />
        </main>
      </div>
      <ContentPanel />
      <Toast />
    </div>
  )
}

export function GuestArtifactViewer(props: GuestArtifactViewerProps) {
  const guestSession: GuestSession = {
    token: props.token,
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
