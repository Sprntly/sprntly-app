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
