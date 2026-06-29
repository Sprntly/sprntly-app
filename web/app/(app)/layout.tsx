import { NavigationProvider } from "../context/NavigationContext"
import { ContentProvider } from "../context/ContentContext"
import { CompanyProvider } from "../context/CompanyContext"
import { WorkspaceProvider } from "../context/WorkspaceContext"
import { AuthGate } from "./AuthGate"
import { OnboardingRequiredGuard } from "./OnboardingRequiredGuard"
import { AppShell } from "./AppShell"

export default function AppRouteLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGate>
      <WorkspaceProvider>
        <OnboardingRequiredGuard>
          <NavigationProvider>
            <CompanyProvider>
              <ContentProvider>
                <AppShell>{children}</AppShell>
              </ContentProvider>
            </CompanyProvider>
          </NavigationProvider>
        </OnboardingRequiredGuard>
      </WorkspaceProvider>
    </AuthGate>
  )
}
