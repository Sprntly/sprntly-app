import type { Metadata } from "next"
import "./globals.css"
import "./components/design-agent/design-agent.css"
import { AuthProvider } from "./lib/auth"
import SplashRemover from "./components/SplashRemover"

export const metadata: Metadata = {
  title: "Sprntly",
  // The app shipped with NO favicon at all — every tab showed the browser's
  // blank-document glyph, which is the one piece of branding a user sees
  // whether or not they are looking at the product.
  //
  // Declared explicitly against `/public` rather than relying on Next's
  // `app/icon.png` file convention: this is a STATIC EXPORT, and an explicit
  // link tag is the form that survives `output: "export"` with no build-time
  // route generation involved.
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/brand/favicon-16.png", type: "image/png", sizes: "16x16" },
      { url: "/brand/favicon-32.png", type: "image/png", sizes: "32x32" },
      { url: "/brand/favicon-48.png", type: "image/png", sizes: "48x48" },
      { url: "/brand/favicon-64.png", type: "image/png", sizes: "64x64" },
    ],
    apple: [{ url: "/brand/apple-touch-icon-180.png", sizes: "180x180" }],
  },
}

// Critical CSS inlined in <head> so the very first paint is a full white
// loading screen — before globals.css or the client bundle load. This replaces
// the brief black/dark flash the browser would otherwise show on cold load
// (the html element also carries an inline white background + light color-scheme
// so the viewport canvas is white from the very first frame, before this parses).
const CRITICAL_CSS = `
  html { background: #FFFFFF; }
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
  #app-splash .app-splash__mark { color: #111111; display: block; }
  /* The working animation, duplicated here on purpose. This block is inlined
     in <head> so the first paint has it; globals.css — which carries the same
     keyframes for the rest of the app — has not loaded yet at that point, and
     a cold start is exactly when the splash is on screen. Kept byte-identical
     to the .spr-* rules in globals.css. */
  #app-splash .spr-iris {
    transform-box: fill-box;
    transform-origin: 50% 50%;
  }
  @keyframes sprntlySpin {
    0%   { transform: rotate(0deg); }
    19%  { transform: rotate(29deg); }
    40%  { transform: rotate(75deg); }
    51%  { transform: rotate(125deg); }
    64%  { transform: rotate(250deg); }
    76%  { transform: rotate(375deg); }
    88%  { transform: rotate(433deg); }
    100% { transform: rotate(450deg); }
  }
  @keyframes sprntlyBladeTop    { 0%, 100% { transform: translateY(0); }  50% { transform: translateY(19px); } }
  @keyframes sprntlyBladeRight  { 0%, 100% { transform: translateX(0); }  50% { transform: translateX(-19px); } }
  @keyframes sprntlyBladeBottom { 0%, 100% { transform: translateY(0); }  50% { transform: translateY(-19px); } }
  @keyframes sprntlyBladeLeft   { 0%, 100% { transform: translateX(0); }  50% { transform: translateX(19px); } }
  @media (prefers-reduced-motion: no-preference) {
    #app-splash .spr-iris         { animation: sprntlySpin 1.8s linear infinite; }
    #app-splash .spr-blade-top    { animation: sprntlyBladeTop 1.8s ease-in-out infinite; }
    #app-splash .spr-blade-right  { animation: sprntlyBladeRight 1.8s ease-in-out infinite; }
    #app-splash .spr-blade-bottom { animation: sprntlyBladeBottom 1.8s ease-in-out infinite; }
    #app-splash .spr-blade-left   { animation: sprntlyBladeLeft 1.8s ease-in-out infinite; }
  }
`

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" style={{ colorScheme: "light", backgroundColor: "#FFFFFF" }}>
      <head>
        <meta name="color-scheme" content="light" />
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
        {/* The mark works while the app loads. Inline SVG rather than the
            shared <SprntlyThinkingMark />: this renders in the server HTML
            ahead of any client bundle, which is the whole point of the splash
            — a component import would arrive with React, long after the frame
            this is meant to fill. Geometry is identical to that component's. */}
        <div id="app-splash" aria-hidden="true">
          <svg
            className="app-splash__mark"
            width="56"
            height="56"
            viewBox="0 0 126 126"
            fill="currentColor"
            aria-hidden="true"
          >
            <g className="spr-iris">
              <rect className="spr-blade-top" x="21" y="21" width="60" height="22" />
              <rect className="spr-blade-right" x="83" y="21" width="22" height="60" />
              <rect className="spr-blade-bottom" x="45" y="83" width="60" height="22" />
              <rect className="spr-blade-left" x="21" y="45" width="22" height="60" />
            </g>
          </svg>
        </div>
        <SplashRemover />
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  )
}
