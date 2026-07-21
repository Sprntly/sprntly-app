"use client"

/**
 * Internal transcript review, at a deliberately unguessable path.
 *
 * The obscure URL only keeps this off casual radar — it is NOT the gate. The
 * web app is a static export, so this page's JS is served to anyone who asks
 * for it. Access is enforced by the backend: every /v1/transcripts route needs
 * the shared access code (TRANSCRIPTS_ACCESS_CODE_HASH) and 404s without it.
 *
 * Intentionally not added to lib/routes.ts or any nav — nothing should link here.
 */

import { TranscriptsScreen } from "../components/screens/staff/TranscriptsScreen"

export default function TranscriptReviewPage() {
  return <TranscriptsScreen />
}
