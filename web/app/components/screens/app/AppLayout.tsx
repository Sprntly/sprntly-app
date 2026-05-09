"use client"

import { ReactNode } from "react"
import { Sidebar } from "../../shared/Sidebar"

interface AppLayoutProps {
  children: ReactNode
  style?: React.CSSProperties
  mainStyle?: React.CSSProperties
}

export function AppLayout({ children, style, mainStyle }: AppLayoutProps) {
  return (
    <div className="app" style={style}>
      <Sidebar />
      <main className="main" style={mainStyle}>
        {children}
      </main>
    </div>
  )
}
