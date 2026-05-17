"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import { NavigationProvider, useNavigation } from "./context/NavigationContext"
import { ContentProvider } from "./context/ContentContext"
import { DatasetProvider, useDataset } from "./context/DatasetContext"
import {
  AIBar,
  Toast,
  ApproveModal,
  InviteModal,
  ClaudeDrawer,
  TicketDrawer,
  EmptyDatasetBanner,
} from "./components/shared"
import {
  Onboarding1,
  Onboarding2,
  Onboarding3,
  Onboarding4,
  Onboarding5,
  Onboarding6,
  Onboarding7,
  Onboarding8,
} from "./components/screens/onboarding"
import {
  ChatScreen,
  BriefScreen,
  DetailScreen,
  PrdScreen,
  PastScreen,
  ShippedScreen,
  SettingsScreen,
  TeamScreen,
  ConnectorsScreen,
} from "./components/screens/app"
import { useAuth } from "./lib/auth"
import { useBriefHydration } from "./lib/useBriefHydration"

function AppContent() {
  // Active dataset comes from the DatasetProvider (URL ?dataset=… +
  // localStorage). Sidebar's DatasetSwitcher updates it; we re-hydrate
  // the brief whenever it changes.
  const { activeDataset } = useDataset()
  useBriefHydration(activeDataset)

  const { currentScreen, closeDrawers, closeModal, setShareMenuOpen, setReviewPastOpen } =
    useNavigation()

  useEffect(() => {
    const handleKeydown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        closeDrawers()
        closeModal()
        setShareMenuOpen(false)
        setReviewPastOpen(false)
      }
    }
    document.addEventListener("keydown", handleKeydown)
    return () => document.removeEventListener("keydown", handleKeydown)
  }, [closeDrawers, closeModal, setShareMenuOpen, setReviewPastOpen])

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      if (!target.closest(".share-menu") && !target.closest('[class*="share"]')) {
        setShareMenuOpen(false)
      }
      if (!target.closest(".review-past-menu") && !target.closest(".review-past-wrap")) {
        setReviewPastOpen(false)
      }
    }
    document.addEventListener("click", handleClick)
    return () => document.removeEventListener("click", handleClick)
  }, [setShareMenuOpen, setReviewPastOpen])

  const renderScreen = () => {
    switch (currentScreen) {
      case "ob-1":
        return <Onboarding1 />
      case "ob-2":
        return <Onboarding2 />
      case "ob-3":
        return <Onboarding3 />
      case "ob-4":
        return <Onboarding4 />
      case "ob-5":
        return <Onboarding5 />
      case "ob-6":
        return <Onboarding6 />
      case "ob-7":
        return <Onboarding7 />
      case "ob-8":
        return <Onboarding8 />
      case "chat":
        return <ChatScreen />
      case "brief":
        return <BriefScreen />
      case "detail":
        return <DetailScreen />
      case "prd":
        return <PrdScreen />
      case "ondemand":
        return <ChatScreen />
      case "past":
        return <PastScreen />
      case "shipped":
        return <ShippedScreen />
      case "settings":
        return <SettingsScreen />
      case "team":
        return <TeamScreen />
      case "connectors":
        return <ConnectorsScreen />
      default:
        return <ChatScreen />
    }
  }

  return (
    <>
      <EmptyDatasetBanner />
      {renderScreen()}
      <AIBar />
      <Toast />
      <ApproveModal />
      <InviteModal />
      <ClaudeDrawer />
      <TicketDrawer />
    </>
  )
}

function AuthGate({ children }: { children: React.ReactNode }) {
  const auth = useAuth()
  const router = useRouter()

  useEffect(() => {
    if (auth.kind === "anonymous") {
      router.replace("/sign-in")
    }
  }, [auth.kind, router])

  if (auth.kind !== "authed") {
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

  return <>{children}</>
}

export default function HomePage() {
  return (
    <AuthGate>
      <DatasetProvider>
        <NavigationProvider>
          <ContentProvider>
            <AppContent />
          </ContentProvider>
        </NavigationProvider>
      </DatasetProvider>
    </AuthGate>
  )
}
