// Public viewer layout for the whole `/r` subtree (the shared-report surface).
//
// INTENTIONALLY minimal, mirroring `web/app/p/layout.tsx`: it mounts NONE of the
// authenticated app's providers — no AuthGate, NavigationProvider,
// ContentProvider, CompanyProvider, WorkspaceProvider or AppShell. Those are
// auth-coupled and live only under the `(app)` route group; `web/app/r/` sits
// OUTSIDE that group, so it bypasses AuthGate by construction. The page must
// render for a visitor with zero session — that is the whole point of a share
// link.
import type { ReactNode } from "react"

export default function PublicReportLayout({ children }: { children: ReactNode }) {
  return <div className="report-public-root">{children}</div>
}
