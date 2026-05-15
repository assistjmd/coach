import AuthenticationServices
import SwiftUI

struct SignInWithAppleButtonView: View {
    @EnvironmentObject var session: SessionStore
    @State private var error: String?

    var body: some View {
        VStack(spacing: 16) {
            SignInWithAppleButton(.signIn) { request in
                request.requestedScopes = [.email, .fullName]
            } onCompletion: { result in
                Task { await handle(result) }
            }
            .signInWithAppleButtonStyle(.black)
            .frame(height: 50)

            if let error {
                Text(error).font(.footnote).foregroundStyle(.red)
            }
        }
    }

    private func handle(_ result: Result<ASAuthorization, Error>) async {
        switch result {
        case .failure(let err):
            error = err.localizedDescription
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
                self.error = error.localizedDescription
            }
        }
    }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}
