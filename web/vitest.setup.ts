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

// @testing-library's async utilities (waitFor, findBy*) give an assertion a
// 1000ms budget by default. That is plenty of wall-clock for our tests — every
// awaited call in them is a resolved mock, so the assertion passes on the first
// poll — but it is NOT a budget on the assertion, it is a budget on the whole
// process getting scheduled. Under CI's 272-file run the worker is memory-
// pressured (see the scrollIntoView note above: this suite has OOM'd), and a GC
// pause long enough to eat 1000ms of polling turns a passing assertion into
// "expected spy to be called with ...". That flake cost ~12% of web CI runs
// across unrelated branches, always on a waitFor.
//
// Raising the ceiling costs nothing when tests pass: waitFor resolves as soon
// as the assertion holds, so the timeout only bounds FAILURES. vitest.config's
// testTimeout is set well above this so a genuinely stuck assertion still
// reports its own error rather than a bare "test timed out".
//
// Guarded on `document`: the setup file also runs for the `node`-environment
// tests (vitest.config sets environment: "node"; DOM tests opt in with a
// `@vitest-environment jsdom` docblock), and @testing-library/dom wants a DOM.
if (typeof document !== "undefined") {
  const { configure } = await import("@testing-library/dom")
  configure({ asyncUtilTimeout: 5000 })
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
