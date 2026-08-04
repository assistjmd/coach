import AuthenticationServices
import SwiftUI

struct SignInWithAppleButtonView: View {
    @EnvironmentObject var session: SessionStore
    @State private var error: String?
    /// When the authorization request started. A failure that arrives almost
    /// immediately means iOS never presented the sheet — indistinguishable from a
    /// real user cancellation by error code alone, since both report 1001.
    @State private var startedAt: Date?

    var body: some View {
        VStack(spacing: 16) {
            SignInWithAppleButton(.signIn) { request in
                startedAt = Date()
                request.requestedScopes = [.email, .fullName]
            } onCompletion: { result in
                Task { await handle(result) }
            }
            // `.black` is invisible against this screen's black background: the
            // button chrome vanishes and only the label shows.
            .signInWithAppleButtonStyle(.white)
            .frame(height: 50)

            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
                    .textSelection(.enabled)
            }
        }
    }

    private func handle(_ result: Result<ASAuthorization, Error>) async {
        switch result {
        case .failure(let err):
            error = diagnose(err)
        case .success(let auth):
            guard let cred = auth.credential as? ASAuthorizationAppleIDCredential,
                  let tokenData = cred.identityToken,
                  let token = String(data: tokenData, encoding: .utf8) else {
                error = "missing identity token"
                return
            }
            let displayName = [cred.fullName?.givenName, cred.fullName?.familyName]
                .compactMap { $0 }
                .joined(separator: " ")
                .nilIfEmpty
            do {
                let resp: APIClient.AuthResponse = try await APIClient.shared.post(
                    "/v1/auth/apple",
                    body: ["identity_token": token, "display_name": displayName as Any]
                )
                session.signIn(token: resp.accessToken, userId: resp.userId)
            } catch {
                self.error = "Backend rejected the token: \(error.localizedDescription)"
            }
        }
    }

    /// `localizedDescription` renders every one of these as "The operation couldn't
    /// be completed", which says nothing about what to fix. Decode the code instead.
    private func diagnose(_ err: Error) -> String {
        let elapsed = startedAt.map { Date().timeIntervalSince($0) } ?? -1
        let ms = String(format: "%.0f", max(elapsed, 0) * 1000)
        let ns = err as NSError

        guard ns.domain == ASAuthorizationError.errorDomain,
              let code = ASAuthorizationError.Code(rawValue: ns.code) else {
            return "Unexpected error — domain \(ns.domain), code \(ns.code): \(ns.localizedDescription)"
        }

        let detail: String
        switch code {
        case .canceled:
            // Under ~300ms there was no time for a sheet to appear, let alone be
            // dismissed — so this is iOS declining, not a tap.
            if elapsed >= 0, elapsed < 0.3 {
                detail = """
                    iOS refused to show the sign-in sheet — it failed in \(ms)ms, too \
                    fast for a sheet to have appeared. This is an account or device \
                    condition rather than a cancellation. Check that you are signed \
                    into iCloud, that two-factor auth is on for that Apple ID, and \
                    that Screen Time → Content & Privacy → Account Changes is Allow.
                    """
            } else {
                detail = "Sign-in was cancelled after \(String(format: "%.1f", elapsed))s — the sheet did appear."
            }
        case .invalidResponse:
            detail = "Apple returned an invalid response. Usually transient — retry."
        case .notHandled:
            detail = "Request not handled. Often means the app's Sign in with Apple entitlement doesn't match its App ID."
        case .failed:
            detail = "Authorization failed. Check reachability to Apple's ID servers."
        case .notInteractive:
            detail = "The request needed to present UI but could not."
        case .unknown:
            detail = "Apple reported an unknown failure."
        @unknown default:
            detail = "Unhandled authorization error."
        }

        return "\(detail)\n\n[code \(ns.code) · \(ms)ms]"
    }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}
