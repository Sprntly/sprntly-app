// Bundle-proxy view-grant flow — PUBLIC-PATH NON-REGRESSION (plan §1.4 / §11).
//
// The public `/p/<token>` surface is token-in-URL (F6): the share token IS the
// access primitive and travels on every asset GET in the URL path. It MUST NOT
// mint a `da_view_grant` cookie — that grant flow is the AUTHED surface only.
//
// This locks the invariant at the source level (node-env, read-from-disk — the
// repo's CSS/Viewer-test convention) so a future edit that wires the authed
// grant into the public viewer trips the guard. The authed grant lives in
// useViewGrant + PostGenerationResult (the authed container), NOT here.
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

const HERE = dirname(fileURLToPath(import.meta.url))
const P_DIR = join(HERE, "..")

function read(name: string): string {
  return readFileSync(join(P_DIR, name), "utf8")
}

describe("public /p/<token> viewer does NOT mint a view-grant", () => {
  it("PublicTokenViewer never references viewGrant / useViewGrant / onBundleAssetError", () => {
    const src = read("PublicTokenViewer.tsx")
    expect(src).not.toMatch(/viewGrant/)
    expect(src).not.toMatch(/useViewGrant/)
    expect(src).not.toMatch(/onBundleAssetError/)
    // It still loads the bundle straight from the token-resolved bundle_url.
    expect(src).toMatch(/bundleUrl=\{state\.bundleUrl\}/)
  })

  it("PasscodeGate never references the authed view-grant flow", () => {
    const src = read("PasscodeGate.tsx")
    expect(src).not.toMatch(/viewGrant/)
    expect(src).not.toMatch(/useViewGrant/)
    // Passcode loads its bundle from the verify response (POST /passcode), not a
    // bearer view-grant mint.
    expect(src).toMatch(/bundleUrl=\{props\.view\.bundleUrl\}/)
  })
})
