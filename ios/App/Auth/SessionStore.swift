import Foundation
import Combine

@MainActor
final class SessionStore: ObservableObject {
    @Published private(set) var isAuthenticated: Bool = false
    @Published private(set) var userId: String?

    private let keychainKey = "coach.access_token"

    var accessToken: String? {
        Keychain.read(keychainKey)
    }

    func bootstrap() async {
        isAuthenticated = accessToken != nil
    }

    func signIn(token: String, userId: String) {
        Keychain.write(keychainKey, value: token)
        self.userId = userId
        self.isAuthenticated = true
    }

    func signOut() {
        Keychain.delete(keychainKey)
        userId = nil
        isAuthenticated = false
    }
}
