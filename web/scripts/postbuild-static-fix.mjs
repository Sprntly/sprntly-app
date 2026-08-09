// Post-export fixups for the static Next build (output: "export").
//
// A route that has BOTH an index page and child pages (e.g. `/docs` +
// `/docs/[slug]`) exports as `out/docs.html` AND an `out/docs/` directory. The
// nginx config that serves the export uses:
//
//     try_files $uri $uri/ $uri.html /index.html;
//
// For `/docs`, nginx matches `$uri/` (the `out/docs/` directory) BEFORE
// `$uri.html` (`out/docs.html`). That directory has no `index.html` and
// autoindex is off, so nginx returns **403** and never reaches `docs.html`.
// (Child URLs like `/docs/<slug>` are fine — they fall through to `$uri.html`.)
//
// Fix without touching nginx: give each such directory an `index.html` that is
// a copy of its sibling `<name>.html`, so `$uri/` resolves. This ships via the
// normal app deploy (rsync of `out/`) with no server change.
import { copyFileSync, existsSync, mkdirSync } from "node:fs"
import { dirname, resolve } from "node:path"

const OUT = resolve(process.cwd(), "out")

// Each entry: an index page whose route also has child pages. Add more here if
// another route ever grows children (e.g. a second docs-style section).
const INDEX_ROUTES = ["docs"]

let fixed = 0
for (const route of INDEX_ROUTES) {
  const src = resolve(OUT, `${route}.html`)
  const destDir = resolve(OUT, route)
  const dest = resolve(destDir, "index.html")
  if (!existsSync(src)) {
    console.warn(`[postbuild] skip: ${route}.html not found (route removed?)`)
    continue
  }
  mkdirSync(dirname(dest), { recursive: true })
  copyFileSync(src, dest)
  console.log(`[postbuild] ${route}.html -> ${route}/index.html`)
  fixed++
}
console.log(`[postbuild] static-fix done (${fixed} route(s))`)
