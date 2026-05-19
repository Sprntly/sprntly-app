import { NavigationProvider } from "../context/NavigationContext"
import { ContentProvider } from "../context/ContentContext"
import { CompanyProvider } from "../context/CompanyContext"
import { AuthGate } from "./AuthGate"
import { AppShell } from "./AppShell"

export default function AppRouteLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGate>
      <NavigationProvider>
        <CompanyProvider>
          <ContentProvider>
            <AppShell>{children}</AppShell>
          </ContentProvider>
        </CompanyProvider>
      </NavigationProvider>
    </AuthGate>
  )
}
