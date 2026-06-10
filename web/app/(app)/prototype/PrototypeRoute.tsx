"use client"

// Client surface for the dedicated /prototype route. This page is the prototype
// tab's landing: it renders the generate panel as an always-open form, reads the
// PRD context from the URL (?prd=<id>) via useSearchParams, and hands off to the
// refresh-stable canvas route (/prototype/{id}) once generation succeeds.
//
// The normal entry path from the PRD screen is now inline: clicking "Generate
// Prototype" opens the generate modal in-place over the PRD (no navigation).
// This page remains available for direct URL access and can be reached by
// navigating to the prototype tab with a PRD query param. Bare /prototype (no
// ?prd=) shows an empty state prompting the user to choose a PRD first.
//
// Co-located with the page exactly like web/app/p/[token]/PublicTokenViewer.tsx
// and web/app/(app)/onboarding/[step]/OnboardingStep.tsx — the server shell
// (page.tsx) satisfies static export; this owns the runtime behaviour. The PRD
// context is read from the URL client-side so no per-id dynamic segment is needed.
//
// The generation surface reuses the same GenerateModal as the approve flow (real
// connector/figma/repo wiring, the shared runGenerateFlow via
// designAgentApi.generate), rendered as the always-open panel. The
// GenerationLoadingScreen overlay provides kickoff-to-ready feedback. The
// figma_file_key is pulled from ContentContext when the loaded PRD matches the
// URL's prd id; it degrades to null otherwise.
//
// Lives in the (app) group → behind AuthGate, matching the canvas route: this is
// an authed internal authoring surface.
import { useState, useRef } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useNavigation } from "../../context/NavigationContext"
import { useContent } from "../../context/ContentContext"
import { canvasPath, prdIdFromPrototypeSearch } from "../../lib/routes"
import { GenerateModal } from "../../components/design-agent/GenerateModal"
import { GenerationLoadingScreen } from "../../components/design-agent/GenerationLoadingScreen"
import type { DesignAgentGenResult } from "../../lib/runDesignAgentGeneration"

/** Pure: build the modal onClose handler that is safe to capture as a closure.
 *  Navigation only fires when no generation is in flight. The loading state is
 *  read via a getter (in the real component: `() => genLoadingRef.current`) so
 *  the closure always sees the live value rather than the stale value captured at
 *  render time — a ref read is immune to React's closure-over-state timing.
 *  Exported for unit testing without a DOM (Node env, no jsdom needed).
 */
export function buildGatedOnClose(
  getLoading: () => boolean,
  navigate: () => void,
): () => void {
  return () => {
    if (!getLoading()) navigate()
  }
}

/** Pure: resolve the figma_file_key to seed the generate panel with, given the
 *  URL's prd id and the PRD currently loaded in ContentContext. Returns the
 *  content PRD's figma_file_key ONLY when its prd_id matches the URL (so a stale
 *  PRD from a prior screen never leaks its source), else null. Extracted +
 *  exported so it is unit-testable without a DOM (the repo's vitest env is node).
 */
export function figmaKeyForPrototype(
  urlPrdId: number | null,
  contentPrd: { prd_id: number; figma_file_key?: string | null } | null,
): string | null {
  if (urlPrdId == null || !contentPrd) return null
  if (contentPrd.prd_id !== urlPrdId) return null
  return contentPrd.figma_file_key ?? null
}

export function PrototypeRoute() {
  const router = useRouter()
  const search = useSearchParams()
  const { goTo } = useNavigation()
  const { content } = useContent()

  const prdId = prdIdFromPrototypeSearch(search.get("prd"))
  const figmaFileKey = figmaKeyForPrototype(prdId, content.prd)

  // Ref-backed loading flag: read live inside the onClose closure so the
  // callback never captures a stale false from the render before kickoff.
  const genLoadingRef = useRef(false)

  // Full-screen loading-overlay visibility + the prototype_id known once the
  // generate POST returns (lets the loading screen subscribe to the SSE stream).
  const [genLoading, setGenLoading] = useState(false)
  const [genFigmaKey, setGenFigmaKey] = useState<string | null>(null)
  const [genGithubRepo, setGenGithubRepo] = useState<string | null>(null)
  const [genProtoId, setGenProtoId] = useState<number | null>(null)

  // Show the overlay the instant generation kicks off; capture the source context
  // for the loading screen's source-aware steps.
  const handleGenStart = (ctx?: {
    figmaFileKey?: string | null
    githubRepo?: string | null
  }) => {
    setGenFigmaKey(ctx?.figmaFileKey ?? null)
    setGenGithubRepo(ctx?.githubRepo ?? null)
    setGenProtoId(null)
    genLoadingRef.current = true
    setGenLoading(true)
  }

  // Terminal generation outcome. On SUCCESS hand off to the refresh-stable canvas
  // route for the generated prototype (navigates to /prototype/{id} — the canvas
  // route that coexists with this landing under the same /prototype base). On
  // FAILURE / no result, just dismiss the overlay; the panel stays so the user
  // can retry (runGenerateFlow already toasted the failure).
  const handleGenDone = (result?: DesignAgentGenResult) => {
    genLoadingRef.current = false
    setGenLoading(false)
    if (result?.ok && result.prototype) {
      router.push(canvasPath(result.prototype.id))
    }
  }

  // No PRD context (bare /prototype): there is nothing to generate from. Send the
  // user to the PRD screen to pick/approve a PRD first, rather than mounting a
  // generate panel with prdId === null (the Generate button is disabled there
  // anyway). Keeps the page honest about its single job.
  if (prdId == null) {
    return (
      <div className="design-agent-surface da-prototype-empty" data-testid="prototype-route-empty">
        <h2 className="da-prototype-empty-title">No PRD selected</h2>
        <p className="da-prototype-empty-sub">
          Open a PRD and choose “Generate Prototype” to start a prototype here.
        </p>
        <button type="button" className="btn btn-accent" onClick={() => goTo("prd")}>
          Go to PRD
        </button>
      </div>
    )
  }

  return (
    <div className="design-agent-surface da-prototype-page" data-testid="prototype-route">
      {/* The generation surface — the SAME GenerateModal the Approve flow used,
          rendered as the always-open panel on this dedicated page. */}
      <GenerateModal
        open
        onClose={buildGatedOnClose(
          () => genLoadingRef.current,
          () => router.push("/prd"),
        )}
        prdId={prdId}
        figmaFileKey={figmaFileKey}
        onGenStart={handleGenStart}
        onKickoff={(id) => setGenProtoId(id)}
        onGenDone={handleGenDone}
      />
      <GenerationLoadingScreen
        open={genLoading}
        figmaFileKey={genFigmaKey}
        githubRepo={genGithubRepo}
        prototypeId={genProtoId}
      />
    </div>
  )
}
