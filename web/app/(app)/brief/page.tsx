"use client"

import { ChatScreen } from "../../components/screens/app/ChatScreen"

// The Weekly/Monday Brief is now the pinned first TAB of the unified home
// surface (ChatScreen). `/brief` renders the SAME unified surface as `/`;
// ChatScreen detects the brief screen (via NavigationContext.currentScreen) and
// activates the pinned brief tab, so the sidebar "Monday brief" item lands here
// coherently whether the surface is freshly mounted or already on screen. The
// old standalone BriefScreen is intentionally left for a follow-up removal PR.
export default function BriefPage() {
  return <ChatScreen />
}
