import { NavigationProvider } from "../context/NavigationContext"
import { ContentProvider } from "../context/ContentContext"
import { CompanyProvider } from "../context/CompanyContext"
import { WorkspaceProvider } from "../context/WorkspaceContext"
import { AuthGate } from "./AuthGate"
import { OnboardingRequiredGuard } from "./OnboardingRequiredGuard"
import { AppShell } from "./AppShell"
import { PaymentRequiredPrompt } from "../components/shared/PaymentRequiredPrompt"
import { OutOfCreditsBanner } from "../components/shared/OutOfCreditsBanner"

export default function AppRouteLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGate>
      <WorkspaceProvider>
        <OnboardingRequiredGuard>
          <NavigationProvider>
            <CompanyProvider>
              <ContentProvider>
                <AppShell>{children}</AppShell>
                {/* One listener for every billable route's 402, rather than the
                    same branch copied into eight surfaces and forgotten in the
                    ninth. */}
                <PaymentRequiredPrompt />
                {/* The standing version of the same fact. The prompt above
                    fires when a generation is refused; this says so before
                    anyone writes one. Mounted here, beside it, so both read
                    from the shell rather than from whichever screen happens
                    to be open. */}
                <OutOfCreditsBanner />
              </ContentProvider>
            </CompanyProvider>
          </NavigationProvider>
        </OnboardingRequiredGuard>
      </WorkspaceProvider>
    </AuthGate>
  )
}
