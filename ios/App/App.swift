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
        }
    }
}
