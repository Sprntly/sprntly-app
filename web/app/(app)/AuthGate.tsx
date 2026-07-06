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
        background: "#FFFFFF",
        color: "#000000",
        fontFamily: "Geist, system-ui, sans-serif",
        fontSize: 15,
        fontWeight: 500,
      }}
    >
      Loading…
    </div>
  )
}
