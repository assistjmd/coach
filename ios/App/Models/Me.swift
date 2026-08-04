import Foundation

struct ProviderRow: Decodable, Identifiable {
    let slug: String
    let displayName: String
    let connected: Bool
    let lastSyncedAt: Date?
    var id: String { slug }
}

/// `GET /v1/me`. Also the app's launch-state check — see `SessionStore.bootstrap`.
struct MeResponse: Decodable {
    let id: String
    let email: String?
    let displayName: String?
    let timezone: String
    /// Whether the user has finished onboarding. Tracked server-side so a
    /// reinstall or a second device doesn't re-run it.
    let onboarded: Bool
    let providers: [ProviderRow]
}
