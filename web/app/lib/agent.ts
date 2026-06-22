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
export const AGENT_NAME = "Spiky"
