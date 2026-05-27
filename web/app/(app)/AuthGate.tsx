"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "../lib/auth"

export function AuthGate({ children }: { children: React.ReactNode }) {
  const auth = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (auth.kind === "anonymous" || auth.kind === "unconfigured") {
      router.replace("/sign-in")
    }
  }, [auth.kind, router])

  if (auth.kind !== "authed") {
    return (
      <AuthLoading />
    )
  }

  return <>{children}</>
}

function AuthLoading() {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#0a0a0c",
        color: "#7a7a85",
        fontFamily: "Geist, system-ui, sans-serif",
        fontSize: 14,
      }}
    >
      Loading…
    </div>
  )
}
