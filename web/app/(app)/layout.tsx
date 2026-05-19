import { NavigationProvider } from "../context/NavigationContext"
import { ContentProvider } from "../context/ContentContext"
import { AuthGate } from "./AuthGate"
import { AppShell } from "./AppShell"

export default function AppRouteLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGate>
      <NavigationProvider>
        <ContentProvider>
          <AppShell>{children}</AppShell>
        </ContentProvider>
      </NavigationProvider>
    </AuthGate>
  )
}
