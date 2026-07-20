// Vitest's "node" environment does not expose the Web Crypto global the way a
// bare Node process does, so polyfill globalThis.crypto from node:webcrypto.
// Browsers, Edge, and the Node runtime that Next.js builds/serves with all
// provide globalThis.crypto natively; this shim is test-only.
import { webcrypto } from "node:crypto"

if (!globalThis.crypto) {
  Object.defineProperty(globalThis, "crypto", {
    value: webcrypto,
    configurable: true,
  })
}

// jsdom doesn't implement Element.prototype.scrollIntoView. Components that
// auto-scroll a thread (e.g. BriefChat's end-of-thread ref) call it on mount;
// without this stub the call throws, and the unhandled rejections cascade into
// an OOM during the run. Test-only no-op; real browsers provide it natively.
if (
  typeof Element !== "undefined" &&
  !Element.prototype.scrollIntoView
) {
  Element.prototype.scrollIntoView = function scrollIntoView() {}
}

// Chat tabs are now SESSION-scoped (sessionStorage, not localStorage — so a
// fresh open starts with only the pinned Weekly-brief tab). ChatScreen's persist
// effect writes tabs to sessionStorage on every mount, so without a reset a tab
// created in one test would leak into the next (a fresh-mount test would then see
// a stale tab instead of just the brief). Clear it after every test. Guarded by
// `typeof` so the `node`-env tests (which have no global sessionStorage and stub
// their own on `globalThis.window`) are untouched.
import { afterEach } from "vitest"
afterEach(() => {
  if (typeof sessionStorage !== "undefined") {
    try {
      sessionStorage.clear()
    } catch {
      /* storage may be unavailable in some environments; not fatal */
    }
  }
})
