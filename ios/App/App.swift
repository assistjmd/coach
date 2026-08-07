import SwiftUI

@main
struct CoachApp: App {
    @StateObject private var session = SessionStore()
    @StateObject private var health = HealthKitManager()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(session)
                .environmentObject(health)
                .onOpenURL { url in
                    DeepLinkRouter.handle(url, session: session)
                }
                // Re-arm the daily reminder. iOS discards pending requests on
                // reinstall or device restore, and a user who revoked
                // notifications in Settings would otherwise leave our stored
                // preference claiming a reminder that no longer exists.
                .task { await MorningNudge.shared.refresh() }
        }
    }
}
