// Public docs layout for the whole `/docs` subtree. Like `/p`, `/privacy`, and
// `/terms`, this route group sits OUTSIDE `(app)`, so it mounts NONE of the
// authenticated providers (AuthGate, Navigation/Content/Company/Workspace,
// AppShell). The docs render for a visitor with zero session — no login
// required. The root app/layout.tsx supplies <html>/<body> + the inert
// AuthProvider context; nothing here gates.
import type { ReactNode } from "react"

export default function DocsLayout({ children }: { children: ReactNode }) {
  return <div className="docs-root">{children}</div>
}
