"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"

/**
 * Step 4 — superseded by the design-v4 flow: connectors moved to the
 * categorized wizard at step 6 (single connect surface, shared
 * ConnectorConnectModal). This step auto-advances so users mid-flow with
 * a stored onboarding_step of 4 continue seamlessly.
 */
export function Onboarding4() {
  const router = useRouter()
  useEffect(() => {
    router.replace("/onboarding/5")
  }, [router])
  return null
}
