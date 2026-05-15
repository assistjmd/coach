import Foundation

enum DeepLinkRouter {
    static func handle(_ url: URL, session: SessionStore) {
        guard url.scheme == "coach" else { return }
        // coach://oauth/callback?provider=oura&status=ok
        if url.host == "oauth", url.path == "/callback" {
            let comps = URLComponents(url: url, resolvingAgainstBaseURL: false)
            let provider = comps?.queryItems?.first(where: { $0.name == "provider" })?.value
            NotificationCenter.default.post(
                name: .providerConnected,
                object: nil,
                userInfo: ["provider": provider ?? ""]
            )
        }
    }
}

extension Notification.Name {
    static let providerConnected = Notification.Name("coach.providerConnected")
}
