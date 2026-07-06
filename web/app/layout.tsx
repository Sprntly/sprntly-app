import type { Metadata } from "next"
import "./globals.css"
import "./components/design-agent/design-agent.css"
import { AuthProvider } from "./lib/auth"
import SplashRemover from "./components/SplashRemover"

export const metadata: Metadata = {
  title: "Sprntly",
}

// Critical CSS inlined in <head> so the very first paint is a white Sprntly
// loading screen — before globals.css or the client bundle load. This replaces
// the brief black/dark flash the browser would otherwise show on cold load.
const CRITICAL_CSS = `
  html { background: #FFFFFF; color-scheme: light; }
  #app-splash {
    position: fixed;
    inset: 0;
    z-index: 2147483647;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #FFFFFF;
    font-family: 'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    opacity: 1;
    transition: opacity 0.2s ease;
  }
  #app-splash.is-hidden { opacity: 0; pointer-events: none; }
  #app-splash .app-splash__text {
    color: #179463;
    font-size: 15px;
    font-weight: 500;
    letter-spacing: 0.01em;
    animation: app-splash-pulse 1.2s ease-in-out infinite;
  }
  @keyframes app-splash-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }
`

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <head>
        <style dangerouslySetInnerHTML={{ __html: CRITICAL_CSS }} />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://api.fontshare.com/v2/css?f[]=geist@300,400,500,600,700&display=swap"
          rel="stylesheet"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=Geist+Mono:wght@400;500&family=Instrument+Serif:ital@0;1&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <div id="app-splash" aria-hidden="true">
          <span className="app-splash__text">Loading…</span>
        </div>
        <SplashRemover />
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  )
}
