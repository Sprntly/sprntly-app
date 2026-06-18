"use client"

import { Suspense } from "react"
import { ChatScreen } from "../../components/screens/app/ChatScreen"

// The Weekly/Monday Brief is the pinned first TAB of the unified home surface
// (ChatScreen). `/brief` renders the SAME unified surface as `/`; ChatScreen
// detects the brief screen (via NavigationContext.currentScreen) and activates
// the pinned brief tab, so the sidebar "Monday brief" item lands here coherently
// whether the surface is freshly mounted or already on screen. The old
// standalone BriefScreen has been removed — the brief tab inside ChatScreen
// (which renders <BriefChat/>) is the sole brief surface.
//
// Suspense boundary: ChatScreen reads useSearchParams() (the `?new=1` "New chat"
// hand-off), which Next requires be wrapped for prerender.
export default function BriefPage() {
  return (
    <Suspense fallback={null}>
      <ChatScreen />
    </Suspense>
  )
}
