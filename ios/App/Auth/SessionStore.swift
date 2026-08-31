import Combine
import Foundation

@MainActor
final class SessionStore: ObservableObject {
    enum Phase: Equatable {
        case loading
        case signedOut
        case onboarding
        case ready
    }

    @Published private(set) var phase: Phase = .loading
    @Published private(set) var userId: String?

    private let keychainKey = "coach.access_token"
    private let userIdKey = "coach.user_id"

    var accessToken: String? { Keychain.read(keychainKey) }
    var isAuthenticated: Bool { phase == .onboarding || phase == .ready }

    init() {
        // Persisted so it survives a cold launch. Previously `userId` lived only
        // in memory, so after a relaunch the user was authenticated but their id
        // was nil.
        userId = Keychain.read(userIdKey)
    }

    /// Resolve the launch state against the server.
    ///
    /// This deliberately makes a network call rather than trusting the presence
    /// of a keychain token. A token can be expired or revoked, and until now
    /// nothing in the app ever noticed — every screen would just show its own
    /// error string forever. Asking `/v1/me` gives us both the onboarding state
    /// and a real 401 check in one round trip.
    /// Keep the server's idea of the user's timezone matching the device.
    ///
    /// It was written once at onboarding and never again, so anyone who moved —
    /// or who signed up while travelling — kept getting their brief at the wrong
    /// local hour permanently. Checked on every launch, sent only on a change, so
    /// this is normally zero requests.
    ///
    /// Failures are ignored on purpose: a wrong timezone is a minor annoyance,
    /// and it must never be the reason someone can't open the app.
    private func syncTimezoneIfChanged(stored: String) async {
        let device = TimeZone.current.identifier
        guard device != stored else { return }
        struct Empty: Decodable {}
        _ = try? await APIClient.shared.put(
            "/v1/me/timezone", body: ["timezone": device]
        ) as Empty
    }

    func bootstrap() async {
        guard accessToken != nil else {
            phase = .signedOut
            return
        }
        do {
            let me: MeResponse = try await APIClient.shared.get("/v1/me")
            userId = me.id
            Keychain.write(userIdKey, value: me.id)
            phase = me.onboarded ? .ready : .onboarding
            await syncTimezoneIfChanged(stored: me.timezone)
            // Point the reminder at when this person is actually up, unless
            // they have chosen a time themselves.
            await MorningNudge.shared.alignToMorning(
                wokeAt: me.wokeAt, usualTrainingHour: me.usualTrainingHour
            )
        } catch APIError.http(401, _) {
            // The token is no longer good. Clear it rather than leaving the app
            // in a state where every request fails.
            signOut()
        } catch {
            // Network trouble is not an auth problem — assume the stored session
            // is still valid and let individual screens surface their own errors.
            // Treating a flaky connection as a sign-out would be far worse.
            phase = .ready
        }
    }

    func signIn(token: String, userId: String) {
        Keychain.write(keychainKey, value: token)
        Keychain.write(userIdKey, value: userId)
        self.userId = userId
        // New sessions start in onboarding; `bootstrap()` corrects this to
        // `.ready` for anyone who has already been through it.
        phase = .onboarding
        Task { await bootstrap() }
    }

    func finishOnboarding() {
        phase = .ready
    }

    func signOut() {
        Keychain.delete(keychainKey)
        Keychain.delete(userIdKey)
        userId = nil
        phase = .signedOut
    }
}
