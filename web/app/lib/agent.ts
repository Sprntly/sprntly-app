/** The PM agent's fixed display name.
 *
 *  The agent is no longer user-named: there is ONE display name shown
 *  everywhere the PM agent is rendered (brief/chat header, the chat thread
 *  author label, the AI bar). No user can rename their agent, so callers must
 *  use this constant rather than reading any stored per-company/per-user name.
 *
 *  Note: the "PM AGENT" / "PM COWORKER" / "DS AGENT" pills elsewhere are *role*
 *  badges (the agent's function), not its name — they are intentionally
 *  unaffected by this constant.
 */
export const AGENT_NAME = "Sprntly"

/** The agent's role badge next to its name in a chat thread.
 *
 *  ONE label on every chat surface — main chat renders "Product Coworker"
 *  and the project surfaces read this constant so they can never drift to a
 *  different label (the group chat's old "AGENT" badge / private's missing
 *  badge were exactly that drift). */
export const AGENT_BADGE = "Product Coworker"
