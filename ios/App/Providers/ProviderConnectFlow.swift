import AuthenticationServices
import Foundation
import UIKit

@MainActor
final class ProviderConnectFlow: NSObject, ASWebAuthenticationPresentationContextProviding {
    static let shared = ProviderConnectFlow()

    func connect(providerSlug: String) async throws {
        guard let token = Keychain.read("coach.access_token") else {
            throw APIError.http(401, "not signed in")
        }
        let url = APIClient.shared.baseURL
            .appendingPathComponent("/v1/providers/\(providerSlug)/authorize")

        // We pass the bearer token via a one-shot custom scheme — backend's authorize
        // endpoint expects it in the Authorization header on the redirect-issuing request.
        // ASWebAuthenticationSession doesn't let us set headers, so we hit the endpoint
        // ourselves first to capture the 302 location, then open Safari at that URL.
        var req = URLRequest(url: url)
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let session = URLSession(configuration: .ephemeral)
        let (_, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse,
              let loc = http.value(forHTTPHeaderField: "Location"),
              let providerURL = URL(string: loc) else {
            throw APIError.http(0, "missing redirect")
        }

        try await openWebAuth(start: providerURL, callbackScheme: "coach")
    }

    private func openWebAuth(start: URL, callbackScheme: String) async throws {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            let session = ASWebAuthenticationSession(
                url: start,
                callbackURLScheme: callbackScheme
            ) { _, error in
                if let error { cont.resume(throwing: error); return }
                cont.resume()
            }
            session.presentationContextProvider = self
            session.prefersEphemeralWebBrowserSession = false
            session.start()
        }
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        // Use the first connected scene's key window
        let scenes = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
        return scenes.flatMap(\.windows).first(where: { $0.isKeyWindow }) ?? ASPresentationAnchor()
    }
}
