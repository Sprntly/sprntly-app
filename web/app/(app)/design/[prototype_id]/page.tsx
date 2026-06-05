// P7-05 (D3) — refresh-stable canvas route shell. Thin SERVER shell mirroring
// web/app/p/[token]/page.tsx (and web/app/(app)/onboarding/[step]/page.tsx): a
// server component that satisfies static export, delegating runtime behaviour to
// a co-located client component.
//
// WHY a shell at all: next.config.ts uses `output: "export"` — the web app is a
// static SPA with no server runtime, and the prototype_id is unbounded (arbitrary
// integers), so static export cannot prerender a page per id. This shell is
// emitted once under a sentinel param; DesignCanvasRoute reads the REAL
// prototype_id from the URL at runtime. The canvas itself is resolved + revealed
// by the (app)-group ApproveModal (mounted in AppShell), whose URL-driven,
// hydration-gated resolver reads NavigationContext.canvasPrototypeId — so a
// refresh on /design/<id> re-opens the canvas. This shell exists so the dynamic
// route BUILDS and so something renders behind the canvas overlay.
//
// Lives in the (app) group → behind AuthGate: the canvas is an authed internal
// authoring surface, NOT the public /p/[token] viewer.
//
// EXTERNAL DEPENDENCY (recorded in the PR, NOT a blocker): serving an arbitrary
// /design/<id> on the static host on a REAL hard-refresh relies on a deploy-side
// SPA-fallback rewrite (/design/* → this shell) — same class as the open /p/*
// rewrite. Until that lands, a hard-refresh 404s; this is KNOWN and flag-gated
// (NEXT_PUBLIC_DESIGN_AGENT_ENABLED=0), not a bug.
import { DesignCanvasRoute } from "./DesignCanvasRoute"

// Static export needs ≥1 param to emit the shell. The value is a build-time
// placeholder only — never read at runtime (the client reads the URL's id).
export function generateStaticParams() {
  return [{ prototype_id: "_" }]
}

export default function DesignCanvasPage() {
  return <DesignCanvasRoute />
}
