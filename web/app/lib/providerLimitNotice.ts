// Telling the user when the AI provider refused the request.
//
// Every surface in this product degrades gracefully when the LLM provider says
// no — the chat falls open to an answer, the planner answers unplanned, the
// classifier answers directly. Each of those is correct on its own, and the
// sum of them is an app that looks broken for no stated reason. Observed
// 2026-08-16: the Anthropic balance ran out, commands stopped being acted on
// entirely, and the only evidence anywhere was a line in a container log.
//
// This is the shared read of "did the provider refuse, and what do we say" so
// the chat, the brief chat and any future surface all say the same thing.

import type { AskStatusResponse, ChatIntentEnvelope } from "./api"

/** The classes the backend (`app/llm_errors.py`) emits for a provider refusal.
 *  Kept as a set rather than a union so an unrecognised future code still
 *  reads as "a provider problem" — the copy travels with the response, so a
 *  new code needs no client release to be shown correctly. */
const PROVIDER_CLASSES = new Set([
  "provider_limit",
  "provider_unavailable",
  "provider_error",
])

export type ProviderNotice = {
  code: string
  message: string
  /** True for a limit/quota refusal — the case an ADMIN has to act on, as
   *  opposed to a transient overload the user can simply retry. Drives the
   *  toast's title, so "top this up" and "try again in a minute" are not
   *  presented as the same event. */
  needsAdmin: boolean
}

function build(code: string, message: string): ProviderNotice {
  return { code, message, needsAdmin: code === "provider_limit" }
}

/** The provider notice on a finished ask, or null.
 *
 *  Reads the TYPED class, never the `error` string: matching on message text
 *  would break the moment a provider reworded its errors, which is exactly the
 *  fragility the server-side classifier exists to remove. */
export function providerNoticeFromAsk(
  res: Pick<AskStatusResponse, "status" | "error_class" | "error_message"> | null | undefined,
): ProviderNotice | null {
  if (!res || res.status !== "error") return null
  const code = res.error_class
  if (!code || !PROVIDER_CLASSES.has(code)) return null
  // The message is server-composed; fall back to a neutral line rather than
  // showing nothing if an older backend sends the class without the copy.
  return build(
    code,
    res.error_message
      || "Sprntly's AI provider turned this request away. Try again shortly.",
  )
}

/** The provider notice riding a chat-intent envelope, or null.
 *
 *  This is the QUIET case and the more important one: the ask may still answer
 *  perfectly well, but no command can be recognised while the planner is down,
 *  so without this the user just finds that asking for things stopped working. */
export function providerNoticeFromEnvelope(
  envelope: Pick<ChatIntentEnvelope, "provider_error"> | null | undefined,
): ProviderNotice | null {
  const pe = envelope?.provider_error
  if (!pe?.code || !pe?.message) return null
  return build(pe.code, pe.message)
}

/** Title for the toast. Says WHO has to do something, because "try again" and
 *  "an admin must top up the account" are different instructions and showing
 *  the wrong one wastes the user's time. */
export function providerNoticeTitle(notice: ProviderNotice): string {
  return notice.needsAdmin
    ? "AI provider limit reached"
    : "AI provider unavailable"
}
