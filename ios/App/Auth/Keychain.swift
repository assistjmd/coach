import Foundation
import OSLog
import Security

enum Keychain {
    private static let log = Logger(subsystem: "app.coach", category: "keychain")

    /// Store a value. Returns false if the keychain rejected the write.
    ///
    /// The result is surfaced rather than discarded because a failed write is
    /// otherwise indistinguishable from "no session": the app drops to the
    /// sign-in screen with no clue why. The most common cause is a build with no
    /// keychain entitlement (`CODE_SIGNING_ALLOWED=NO`), which fails with
    /// `errSecMissingEntitlement` (-34018).
    @discardableResult
    static func write(_ key: String, value: String) -> Bool {
        guard let data = value.data(using: .utf8) else { return false }
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: key,
        ]
        SecItemDelete(query as CFDictionary)
        var attrs = query
        attrs[kSecValueData as String] = data
        attrs[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        let status = SecItemAdd(attrs as CFDictionary, nil)
        if status != errSecSuccess {
            log.error("keychain write failed for \(key, privacy: .public): OSStatus \(status)")
            return false
        }
        return true
    }

    static func read(_ key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess else {
            if status != errSecItemNotFound {
                log.error("keychain read failed for \(key, privacy: .public): OSStatus \(status)")
            }
            return nil
        }
        guard let data = item as? Data, let value = String(data: data, encoding: .utf8) else {
            return nil
        }
        return value
    }

    static func delete(_ key: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: key,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
