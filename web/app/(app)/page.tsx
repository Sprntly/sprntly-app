"use client"

import { Suspense } from "react"
import { ChatScreen } from "../components/screens/app/ChatScreen"

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
