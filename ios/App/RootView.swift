import SwiftUI
import UIKit

struct RootView: View {
    @EnvironmentObject var session: SessionStore

    var body: some View {
        Group {
            switch session.phase {
            case .loading:
                ScreenBackground {
                    ProgressView().tint(Theme.Palette.accent)
                }
            case .signedOut:
                WelcomeView()
            case .onboarding:
                // Goal capture has to come after sign-in — PUT /v1/goal needs the
                // bearer token — so it is its own phase rather than part of the
                // pre-auth welcome screen.
                OnboardingFlowView()
            case .ready:
                MainTabView()
            }
        }
        .preferredColorScheme(.dark)
        .task { await session.bootstrap() }
    }
}

struct MainTabView: View {
    @EnvironmentObject var health: HealthKitManager

    init() {
        // SwiftUI can't fully style the tab bar, and the translucent default
        // renders as light grey against this background. UIKit appearance is the
        // only way to make it match.
        let appearance = UITabBarAppearance()
        appearance.configureWithOpaqueBackground()
        appearance.backgroundColor = UIColor(Theme.Palette.bg)
        appearance.shadowColor = UIColor(Theme.Palette.line)

        let item = appearance.stackedLayoutAppearance
        item.normal.iconColor = UIColor(Theme.Palette.textDim)
        item.normal.titleTextAttributes = [
            .foregroundColor: UIColor(Theme.Palette.textDim),
            .font: UIFont.systemFont(ofSize: 10, weight: .heavy),
        ]
        item.selected.iconColor = UIColor(Theme.Palette.accent)
        item.selected.titleTextAttributes = [
            .foregroundColor: UIColor(Theme.Palette.accent),
            .font: UIFont.systemFont(ofSize: 10, weight: .heavy),
        ]

        UITabBar.appearance().standardAppearance = appearance
        UITabBar.appearance().scrollEdgeAppearance = appearance
    }

    var body: some View {
        TabView {
            DashboardView()
                .tabItem { Label("TODAY", systemImage: "bolt.fill") }
            ChatView()
                .tabItem { Label("COACH", systemImage: "text.bubble.fill") }
            ConnectionsView()
                .tabItem { Label("SOURCES", systemImage: "antenna.radiowaves.left.and.right") }
            ProfileView()
                .tabItem { Label("GOAL", systemImage: "target") }
        }
        .tint(Theme.Palette.accent)
        // Re-arm HealthKit observers on launch for users who already granted
        // access. This never prompts — onboarding owns the one-shot sheet.
        .task { await health.resumeIfAuthorized() }
    }
}
