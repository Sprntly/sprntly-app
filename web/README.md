# sprntly mockup (Next.js + React)

Full React component port of `../sprntly-app-v4.html` running inside Next.js 15
(App Router). All 18 screens and chrome interactions work; **data cards start
empty** until you hydrate `ContentContext` from your API / LLM.

## Run

```bash
cd web-mockup
npm install   # only needed once
npm run dev
```

Visit <http://localhost:3000>. Use the picker bar at the top or the sidebar to
move between screens.

## How it works

Every screen and UI element is now a proper React component using `useState`,
`useContext`, and `useEffect` hooks.

### Architecture

```
web-mockup/
├── app/
│   ├── context/
│   │   ├── NavigationContext.tsx   # Screens, modals, drawers, toast, AI bar text
│   │   └── ContentContext.tsx      # Product data (brief, shipped, team, …) — empty by default
│   ├── components/
│   │   ├── shared/                 # Reusable UI elements
│   │   │   ├── Sidebar.tsx
│   │   │   ├── AIBar.tsx
│   │   │   ├── Toast.tsx
│   │   │   ├── Picker.tsx          # Dev-mode screen switcher
│   │   │   ├── ApproveModal.tsx
│   │   │   ├── InviteModal.tsx
│   │   │   ├── ClaudeDrawer.tsx
│   │   │   └── TicketDrawer.tsx
│   │   └── screens/
│   │       ├── onboarding/         # ob-1 through ob-8
│   │       │   ├── OnboardingLayout.tsx
│   │       │   └── Onboarding{1-8}.tsx
│   │       └── app/                # Main app screens
│   │           ├── AppLayout.tsx
│   │           ├── ChatScreen.tsx
│   │           ├── BriefScreen.tsx
│   │           ├── DetailScreen.tsx
│   │           ├── PrdScreen.tsx
│   │           ├── PastScreen.tsx
│   │           ├── ShippedScreen.tsx
│   │           ├── SettingsScreen.tsx
│   │           ├── TeamScreen.tsx
│   │           └── ConnectorsScreen.tsx
│   ├── types.ts                    # ScreenId, AI_CONTEXTS, CONNECTOR_STAGES
│   ├── types/content.ts            # Serializable shapes for `setContent`
│   ├── globals.css                 # Design system (lifted from HTML)
│   ├── layout.tsx                  # Google Fonts + metadata
│   └── page.tsx                    # NavigationProvider + ContentProvider + AppContent
├── public/
│   └── sprntly.js                  # (no longer used — kept for reference)
├── package.json
├── tsconfig.json
└── next.config.ts
```

### Navigation

All navigation is controlled by the `NavigationContext`:

```tsx
const { currentScreen, goTo } = useNavigation()
goTo("brief")  // switches to the brief screen
```

### Product data (`ContentContext`)

Screens read mock-free data from `ContentContext`. The default is **empty**:
no findings, no shipped ledger, no team rows, no connector catalog, no PRD
body, no evidence detail, no AI suggestion chips (until you set them).

```tsx
"use client"
import { useEffect } from "react"
import { useContent } from "@/app/context/ContentContext"

function Loader() {
  const { setContent } = useContent()

  useEffect(() => {
    void (async () => {
      const res = await fetch("/api/brief")
      const brief = await res.json()
      setContent({ brief })
    })()
  }, [setContent])
}
```

Use **`setContent(partial)`** to shallow-merge top-level keys, or
**`replaceContent(next)`** to swap the entire `AppContentState`. Shapes live in
`app/types/content.ts` (e.g. `BriefState`, `ShippedState`, `DetailState`, `PrdState`).

**AI bar chips:** set `aiScreenChips: { brief: ["Why is #1 ranked…", ...] }`.
If omitted for a screen, no chips render (unlike the old static `AI_CONTEXTS`
suggestions).

**Sidebar badges:** `sidebarBriefCount` and `sidebarConvCount` are `null` by
default so counts are hidden until you set them.

### State management (navigation UI only)

- **currentScreen**: Which screen is visible
- **activeDrawer**: `"claude"` | `"ticket"` | `null`
- **activeModal**: `"approve"` | `"invite"` | `null`
- **toast**: Toast notification state
- **aiBarValue**: Value in the AI input bar
- **shareMenuOpen** / **reviewPastOpen**: Dropdown state

## What's here

- All 18 screens from the HTML prototype
- Full UI interactivity: modals, drawers, toasts, toggles, connector wizard
- AI bar with `⌘K` focus; suggestion chips only when you set `aiScreenChips`
- Share menu, review-past dropdown (disabled until `pastWeeks` has data)
- **Empty product surfaces** with dashed placeholders until `setContent` runs

## What's NOT here

- No live API: fetch in a parent or route handler and call `setContent` / `replaceContent`.
- No Supabase, no Fly worker, no auth.
- No mobile breakpoints (the original HTML is desktop-only).
- No accessibility primitives — modals don't focus-trap, toasts don't announce.
- The PRD body uses `contentEditable` when a draft exists; empty state shows a
  placeholder until `content.prd` is set.

## Next steps to wire data

1. **Add Supabase client** (or your API client) in a small provider or layout.

2. **Hydrate `ContentContext`** after load — map API / LLM JSON to the types in
   `app/types/content.ts` and call `setContent({ ... })`.

3. **Add route-based navigation** — screens still switch via `NavigationContext`;
   you can later mirror routes with `useRouter` if you want URLs per view.

4. **Add auth** — the other `web/` folder shows Supabase middleware patterns.

## Reconciling with the existing `web/` directory

The repo's other `web/` folder has half-built Next.js pages that import from
`@/lib/supabase/...` and `@/lib/worker` — files that aren't checked in and
that talk to the unfinished Fly.io worker. This `web-mockup/` directory is
deliberately decoupled from that. When the backend is real, merge the two
trees: the visual chrome from here, the data fetching from there.
