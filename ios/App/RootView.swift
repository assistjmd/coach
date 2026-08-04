import SwiftUI

struct RootView: View {
    @EnvironmentObject var session: SessionStore

    var body: some View {
        Group {
            switch session.phase {
            case .loading:
                ProgressView()
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
        .task { await session.bootstrap() }
    }
}

struct MainTabView: View {
    @EnvironmentObject var health: HealthKitManager

    var body: some View {
        TabView {
            DashboardView()
                .tabItem { Label("Today", systemImage: "sun.max") }
            ChatView()
                .tabItem { Label("Coach", systemImage: "bubble.left.and.bubble.right") }
            ConnectionsView()
                .tabItem { Label("Connect", systemImage: "link") }
            ProfileView()
                .tabItem { Label("Profile", systemImage: "person.crop.circle") }
        }
        // Re-arm HealthKit observers on launch for users who already granted
        // access. This never prompts — onboarding owns the one-shot sheet.
        .task { await health.resumeIfAuthorized() }
    }
}
