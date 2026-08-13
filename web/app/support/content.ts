/** Support page copy — the public support commitments (hours, first-response
 *  SLA, channels). Zoom Marketplace review requires the app's Support URL to
 *  state how users open a case, the support email, a knowledge base, team
 *  hours, and a first-response SLA — keep all of those present when editing. */

export type SupportSection = {
  id: string
  title: string
  blocks: string[]
}

export const SUPPORT_EMAIL = "build@sprntly.ai"

export const SUPPORT_SECTIONS: SupportSection[] = [
  {
    id: "intro",
    title: "",
    blocks: [
      "We want every question answered and every problem fixed fast. This page explains how to reach the Sprntly support team, when we're available, and how quickly you can expect to hear back — whether you use Sprntly directly or through one of our integrations, such as the Sprntly app for Zoom.",
    ],
  },
  {
    id: "open-a-case",
    title: "1. Open a support case",
    blocks: [
      `Email ${SUPPORT_EMAIL} from any address. Every email opens a support case and is answered by a human on the Sprntly team.`,
      "To help us resolve your issue on the first reply, include: the email address of your Sprntly account, your company or workspace name, what you were doing, what you expected, and what happened instead. Screenshots are always welcome.",
      "If your question is about a connected integration (for example Zoom, Slack, Jira, or Google Drive), mention which integration and — if the issue is a sync — roughly when you connected it.",
    ],
  },
  {
    id: "hours-sla",
    title: "2. Support hours and response times",
    blocks: [
      "Support team hours: Monday to Friday, 9:00 AM – 6:00 PM (US Eastern Time), excluding US public holidays.",
      "First response SLA: we respond to every new support case within 1 business day, and typically much sooner during support hours.",
      "Suspected security issues are handled with priority at security@sprntly.ai, monitored beyond normal support hours.",
    ],
  },
  {
    id: "knowledge-base",
    title: "3. Documentation and knowledge base",
    blocks: [
      "Our documentation covers getting started, connecting integrations, and how each Sprntly surface works. It is available to everyone, no sign-in required, at app.sprntly.ai/docs.",
      "Many integration questions — which plan a provider requires, what data Sprntly reads, how to reconnect an expired connection — are answered there first.",
    ],
  },
  {
    id: "live-support",
    title: "4. Live support",
    blocks: [
      "Sprntly support is email-first: it keeps every case tracked, lets us attach fixes and screenshots, and means nothing gets lost between shifts. We do not currently offer phone support.",
      "Where a case is easier to resolve interactively, our team will offer to schedule a live video session (Zoom or Google Meet) as part of the email thread — at no charge.",
    ],
  },
  {
    id: "other-contacts",
    title: "5. Other contacts",
    blocks: [
      "Privacy questions or data requests: privacy@sprntly.ai (see our Privacy Policy).",
      "Security reports: security@sprntly.ai.",
      `Everything else — sales, partnerships, feedback: ${SUPPORT_EMAIL}.`,
    ],
  },
]
