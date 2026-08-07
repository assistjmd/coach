import Foundation

struct ProviderRow: Decodable, Identifiable {
    let slug: String
    let displayName: String
    let connected: Bool
    let lastSyncedAt: Date?
    /// Why the last sync failed, if it did. Without this a revoked
    /// authorization and a provider with nothing new both render as silence.
    let lastSyncError: String?
    /// False for sources we list but can't connect yet, pending an approval
    /// we don't control. Defaulted so an older client against a newer server —
    /// or the reverse — doesn't fail to decode.
    let available: Bool?
    /// Why it isn't available, and what happens next.
    let note: String?
    var id: String { slug }

    var isAnnounced: Bool { available == false }
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
