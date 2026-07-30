// Static shell for the `/r/<token>` shared-report viewer.
//
// Under `output: "export"` (next.config.ts) a dynamic route needs explicit params
// to emit a file, so this prerenders a single sentinel — `/r/_.html` — and nginx
// rewrites every real `/r/<token>` request to it (see the `location ~ ^/r/[^/]+/?$`
// rule in backend/deploy/nginx.conf). The token below is therefore a build-time
// placeholder that is NEVER read at runtime: the viewer resolves the real token
// from `window.location.pathname` (reportTokenFromPathname.ts), the same
// convention every `/p` shell uses.
import { PublicReportViewer } from "../PublicReportViewer"

export function generateStaticParams() {
  return [{ token: "_" }]
}

export default function PublicReportPage() {
  return <PublicReportViewer />
}
