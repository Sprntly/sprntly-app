"use client"

import { AppLayout } from "./AppLayout"
import { BriefChat } from "../../shared/BriefChat"

export function BriefScreen() {
  // The Weekly Brief is now a single chat surface: BriefChat renders the brief
  // header, the PM-coworker message with the stacked finding cards, the chat
  // thread, and the floating composer — and owns all the evidence/PRD wiring.
  return (
    <AppLayout mainClassName="main--brief-chat">
      <BriefChat />
    </AppLayout>
  )
}
