// The static-export 403 guard.
//
// nginx serves the export with `try_files $uri $uri/ $uri.html /index.html`,
// so `$uri/` is tried BEFORE `$uri.html`. Any route exporting both
// `<name>.html` and a `<name>/` directory therefore resolves to a directory
// with no index, autoindex is off, and nginx answers 403 — never reaching the
// sibling html that holds the actual page.
//
// This shipped: `/artifacts` grew an `artifacts/doc` child, started exporting
// `out/artifacts/`, and every full page load of the artifacts screen 403'd in
// production. Client-side navigation never touches nginx, so it only appeared
// after a reload or an idle session — which is why it went unnoticed.
//
// The postbuild script used to carry a hand-maintained `["docs"]` list. It now
// derives the set from the build output, and this test pins that derivation:
// a hardcoded list would pass a test that only checked `docs`.
import { mkdirSync, writeFileSync, rmSync } from "node:fs"
import { join } from "node:path"
import { tmpdir } from "node:os"
import { afterEach, beforeEach, describe, expect, it } from "vitest"

import { directoriesShadowingAnHtmlSibling } from "../../../scripts/postbuild-static-fix.mjs"

let root: string

beforeEach(() => {
  root = join(tmpdir(), `static-fix-${Date.now()}-${Math.random().toString(36).slice(2)}`)
  mkdirSync(root, { recursive: true })
})
afterEach(() => rmSync(root, { recursive: true, force: true }))

function file(rel: string, body = "x") {
  const abs = join(root, rel)
  mkdirSync(join(abs, ".."), { recursive: true })
  writeFileSync(abs, body)
}

const routesIn = (r: string) =>
  directoriesShadowingAnHtmlSibling(r).map((f: { route: string }) => f.route).sort()

describe("directoriesShadowingAnHtmlSibling", () => {
  it("finds a directory shadowing its html sibling — the 403 shape", () => {
    file("artifacts.html")
    file("artifacts/doc.html")
    expect(routesIn(root)).toEqual(["artifacts"])
  })

  it("finds EVERY such route, not a remembered one", () => {
    // The whole point of deriving: a second route added later is covered
    // without anyone editing a list in a different file.
    file("docs.html")
    file("docs/intro.html")
    file("artifacts.html")
    file("artifacts/doc.html")
    expect(routesIn(root)).toEqual(["artifacts", "docs"])
  })

  it("ignores a directory that already has its own index.html", () => {
    // Already resolvable by `$uri/` — copying over it would be pointless and
    // would overwrite a real page with its sibling.
    file("blog.html")
    file("blog/index.html", "the real index")
    expect(routesIn(root)).toEqual([])
  })

  it("ignores a directory with no html sibling", () => {
    // `/settings/*` children with no `/settings` index page: nginx falls
    // through to `$uri.html` for the children and there is nothing to fix.
    file("settings/billing.html")
    expect(routesIn(root)).toEqual([])
  })

  it("ignores a lone html file with no directory beside it", () => {
    file("pricing.html")
    expect(routesIn(root)).toEqual([])
  })

  it("skips _next, which can never have an html sibling", () => {
    // Thousands of content-hashed assets; scanning them is pure waste, and a
    // stray `_next.html` must never cause the asset tree to be rewritten.
    file("_next.html")
    file("_next/static/chunk.js")
    expect(routesIn(root)).toEqual([])
  })
})
