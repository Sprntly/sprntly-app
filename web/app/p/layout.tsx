// Public viewer layout for the whole `/p` subtree (lifted from
// `p/[token]/layout.tsx` so BOTH the canonical `/p/<slug>/<token>` route and the
// legacy `/p/<token>` redirect inherit `.da-public-root`). INTENTIONALLY
// minimal: it mounts NONE of the authenticated app's providers — no AuthGate,
// NavigationProvider, ContentProvider, CompanyProvider, WorkspaceProvider, or
// AppShell. Those are auth-coupled (they rely on the session cookie / Supabase
// session) and live only under the `(app)` route group. `web/app/p/` sits
// OUTSIDE that group, so it bypasses AuthGate by construction (the root
// app/layout.tsx supplies <html>/<body> + the inert AuthProvider context,
// nothing that gates). The viewer owns whatever local state it needs; the page
// must render for a visitor with zero session.
import type { ReactNode } from "react"

export default function PublicPrototypeLayout({ children }: { children: ReactNode }) {
  return <div className="da-public-root">{children}</div>
}
