import SwiftUI

/// Pre-authentication screen. Everything that needs a bearer token — goal
/// capture, HealthKit priming, provider connect — lives in `OnboardingFlowView`,
/// which runs after sign-in.
struct WelcomeView: View {
    @State private var appeared = false

    var body: some View {
        ScreenBackground {
            VStack(alignment: .leading, spacing: 0) {
                Spacer(minLength: 24)

                VStack(alignment: .leading, spacing: 6) {
                    Text("COACH")
                        .font(.system(size: 46, weight: .black))
                        .tracking(-1)
                        .foregroundStyle(Theme.Palette.text)
                    Text("WILL")
                        .font(.system(size: 46, weight: .black))
                        .tracking(-1)
                        .foregroundStyle(Theme.Palette.accent)
                    Text("Training that reads your data")
                        .font(Theme.Font.body)
                        .foregroundStyle(Theme.Palette.textDim)
                        .padding(.top, 6)
                }
                .opacity(appeared ? 1 : 0)
                .offset(y: appeared ? 0 : 10)

                Spacer(minLength: 32)

                VStack(alignment: .leading, spacing: 0) {
                    Claim("Daily readiness from your HRV, sleep and recent load", index: 0)
                    Divider().overlay(Theme.Palette.line)
                    Claim("A session for today, tuned to what your body did last night", index: 1)
                    Divider().overlay(Theme.Palette.line)
                    Claim("Ask anything — answered with your own numbers", index: 2)
                }
                .opacity(appeared ? 1 : 0)

                Spacer()

                SignInWithAppleButtonView()
                    .padding(.bottom, 32)
            }
            .padding(.horizontal, Theme.Metric.gutter)
        }
        .task {
            withAnimation(.spring(response: 0.6, dampingFraction: 0.85)) { appeared = true }
        }
    }
}

/// One value-proposition line. Numbered rather than bulleted with a checkmark —
/// the green tick list was a large part of the generic feel.
private struct Claim: View {
    let text: String
    let index: Int
    init(_ text: String, index: Int) {
        self.text = text
        self.index = index
    }

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            Text(String(format: "%02d", index + 1))
                .font(.system(size: 13, weight: .heavy).monospacedDigit())
                .foregroundStyle(Theme.Palette.accent)
                .padding(.top, 3)
            Text(text)
                .font(.system(size: 16, weight: .medium))
                .lineSpacing(2)
                .foregroundStyle(Theme.Palette.text)
            Spacer(minLength: 0)
        }
        .padding(.vertical, 16)
    }
}
