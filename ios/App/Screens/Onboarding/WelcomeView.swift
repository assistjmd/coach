import SwiftUI

/// Pre-authentication screen. Everything that needs a bearer token — goal
/// capture, HealthKit priming, provider connect — lives in `OnboardingFlowView`,
/// which runs after sign-in.
struct WelcomeView: View {
    var body: some View {
        VStack(spacing: 32) {
            Spacer()
            VStack(spacing: 8) {
                Text("Coach Will")
                    .font(.system(size: 48, weight: .bold))
                Text("Your AI training partner")
                    .font(.title3)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            VStack(alignment: .leading, spacing: 16) {
                Bullet("Daily readiness brief from your HRV, sleep, and recent load")
                Bullet("Today's prescribed workout, tuned to your data")
                Bullet("Ask anything — Coach answers using your own numbers")
            }
            .padding(.horizontal)
            Spacer()
            SignInWithAppleButtonView()
                .padding(.horizontal)
                .padding(.bottom, 40)
        }
    }
}

private struct Bullet: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        HStack(alignment: .top) {
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
            Text(text)
        }
    }
}
