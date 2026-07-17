"use client"

import { Suspense } from "react"
import dynamic from "next/dynamic"

// ChatScreen is a ~2100-line component — load it as its own async chunk so the
// route shell stays light. Named export, so unwrap it from the module promise.
const ChatScreen = dynamic(() =>
  import("../components/screens/app/ChatScreen").then((m) => m.ChatScreen)
)

// ChatScreen reads useSearchParams() (the `?new=1` "New chat" hand-off), so it
// must sit under a Suspense boundary — Next prerenders this route and errors
// ("useSearchParams() should be wrapped in a suspense boundary") otherwise.
export default function HomePage() {
  return (
    <Suspense fallback={null}>
      <ChatScreen />
    </Suspense>
  )
}
