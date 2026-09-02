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
//
// THE LIST IS DERIVED, NOT MAINTAINED. It used to be a hardcoded `["docs"]`
// with a comment asking whoever added the next such route to remember. They
// did not: `/artifacts` grew an `artifacts/doc` child, started exporting an
// `out/artifacts/` directory, and every full page load of `/artifacts` 403'd
// in production. It survived unnoticed for a while because client-side
// navigation never reaches nginx — only a reload, a bookmark, or a session
// expiry that forces a real request does. A list that must be updated by hand
// in a different file from the one being changed is a list that drifts, and
// the failure is a hard 403 on a real screen, so this now scans the build
// output instead: every `<name>/` directory sitting beside a `<name>.html`
// gets the copy, automatically and forever.
import { copyFileSync, existsSync, readdirSync, statSync } from "node:fs"
import { join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const OUT = resolve(process.cwd(), "out")

// `_next/` holds the content-hashed asset tree — thousands of entries, none of
// which can ever have an `.html` sibling. Skipping it keeps this scan cheap.
const SKIP_DIRS = new Set(["_next"])

/**
 * Every exported directory that shadows a sibling `<name>.html` and has no
 * `index.html` of its own — i.e. exactly the shape nginx 403s on.
 */
export function directoriesShadowingAnHtmlSibling(root) {
  const found = []
  for (const rel of readdirSync(root, { recursive: true })) {
    const relPath = String(rel)
    if (SKIP_DIRS.has(relPath.split(/[\\/]/)[0])) continue

    const dir = join(root, relPath)
    if (!statSync(dir).isDirectory()) continue

    const sibling = `${dir}.html`
    const index = join(dir, "index.html")
    if (existsSync(sibling) && !existsSync(index)) {
      found.push({ route: relPath.replace(/\\/g, "/"), sibling, index })
    }
  }
  return found
}

// Only when run as a script (`node scripts/postbuild-static-fix.mjs`), so a
// test can import the detection above without this writing into `out/`.
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const shadowed = directoriesShadowingAnHtmlSibling(OUT)

  for (const { route, sibling, index } of shadowed) {
    copyFileSync(sibling, index)
    console.log(`[postbuild] ${route}.html -> ${route}/index.html`)
  }

  console.log(`[postbuild] static-fix done (${shadowed.length} route(s))`)
}
