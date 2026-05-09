"use client"

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  type ReactNode,
} from "react"
import type { ScreenId } from "../types"

interface NavigationContextType {
  currentScreen: ScreenId
  goTo: (screen: ScreenId) => void
  
  // Drawer state
  activeDrawer: "claude" | "ticket" | null
  openDrawer: (drawer: "claude" | "ticket") => void
  closeDrawers: () => void
  
  // Modal state
  activeModal: "approve" | "invite" | null
  openModal: (modal: "approve" | "invite") => void
  closeModal: () => void
  
  // Share menu
  shareMenuOpen: boolean
  setShareMenuOpen: (open: boolean) => void
  
  // Review past menu
  reviewPastOpen: boolean
  setReviewPastOpen: (open: boolean) => void
  
  // Toast
  toast: { title: string; sub: string; link?: string } | null
  showToast: (title: string, sub: string, link?: string) => void
  hideToast: () => void
  
  // AI bar
  aiBarValue: string
  setAIBarValue: (value: string) => void

  /** Narrow icon-only rail vs full labels */
  sidebarCollapsed: boolean
  toggleSidebar: () => void
}

const NavigationContext = createContext<NavigationContextType | null>(null)

export function NavigationProvider({ children }: { children: ReactNode }) {
  const [currentScreen, setCurrentScreen] = useState<ScreenId>("ob-1")
  const [activeDrawer, setActiveDrawer] = useState<"claude" | "ticket" | null>(null)
  const [activeModal, setActiveModal] = useState<"approve" | "invite" | null>(null)
  const [shareMenuOpen, setShareMenuOpen] = useState(false)
  const [reviewPastOpen, setReviewPastOpen] = useState(false)
  const [toast, setToast] = useState<{ title: string; sub: string; link?: string } | null>(null)
  const [aiBarValue, setAIBarValue] = useState("")
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

  useEffect(() => {
    try {
      if (localStorage.getItem("sprntly-sidebar-collapsed") === "1") {
        setSidebarCollapsed(true)
      }
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => {
    if (typeof document === "undefined") return
    if (sidebarCollapsed) {
      document.documentElement.setAttribute("data-sidebar-collapsed", "")
    } else {
      document.documentElement.removeAttribute("data-sidebar-collapsed")
    }
  }, [sidebarCollapsed])

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => {
      const next = !prev
      try {
        localStorage.setItem("sprntly-sidebar-collapsed", next ? "1" : "0")
      } catch {
        /* ignore */
      }
      return next
    })
  }, [])

  const goTo = useCallback((screen: ScreenId) => {
    setCurrentScreen(screen)
    setActiveDrawer(null)
    setActiveModal(null)
    setShareMenuOpen(false)
    setReviewPastOpen(false)
    window.scrollTo({ top: 0, behavior: "instant" })
  }, [])

  const openDrawer = useCallback((drawer: "claude" | "ticket") => {
    setActiveDrawer(drawer)
  }, [])

  const closeDrawers = useCallback(() => {
    setActiveDrawer(null)
  }, [])

  const openModal = useCallback((modal: "approve" | "invite") => {
    setActiveModal(modal)
  }, [])

  const closeModal = useCallback(() => {
    setActiveModal(null)
  }, [])

  const showToast = useCallback((title: string, sub: string, link?: string) => {
    setToast({ title, sub, link })
    setTimeout(() => setToast(null), 5500)
  }, [])

  const hideToast = useCallback(() => {
    setToast(null)
  }, [])

  return (
    <NavigationContext.Provider
      value={{
        currentScreen,
        goTo,
        activeDrawer,
        openDrawer,
        closeDrawers,
        activeModal,
        openModal,
        closeModal,
        shareMenuOpen,
        setShareMenuOpen,
        reviewPastOpen,
        setReviewPastOpen,
        toast,
        showToast,
        hideToast,
        aiBarValue,
        setAIBarValue,
        sidebarCollapsed,
        toggleSidebar,
      }}
    >
      {children}
    </NavigationContext.Provider>
  )
}

export function useNavigation() {
  const context = useContext(NavigationContext)
  if (!context) {
    throw new Error("useNavigation must be used within a NavigationProvider")
  }
  return context
}
