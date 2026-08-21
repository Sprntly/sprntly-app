// Pure helper used by the project invite flow — NO React, NO I/O, so it
// tests in a plain node env.

/** True when `s` looks like a bare email — drives the "Invite <email> by
 *  email" affordance. Intentionally permissive (`local@domain.tld`); the
 *  backend's `resolve_candidate` is the real classifier. */
export function isEmailNeedle(s: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s.trim())
}
