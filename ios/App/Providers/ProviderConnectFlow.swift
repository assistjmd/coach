import AuthenticationServices
import Foundation
import UIKit

/// Outcome of a provider connect attempt.
///
/// Cancelling is deliberately not an error: the user tapping "Cancel" in the web
/// view is a normal choice and must not surface as a red banner.
enum ConnectResult: Equatable {
    case connected(String)
    case denied
    case cancelled
    case failed(reason: String)

    /// User-facing message, or nil when there is nothing worth saying.
    var message: String? {
        switch self {
        case .connected, .cancelled:
            return nil
        case .denied:
            return "You declined access, so nothing was connected."
        case .failed(let reason):
            // `reason` is a closed enum from the backend (see routes/providers.py),
            // never provider-supplied text, so it is safe to switch on and safe
            // to show.
            switch reason {
            case "invalid_state":
                return "That link expired. Please try connecting again."
            case "exchange_failed":
                return "The provider rejected the connection. Please try again."
            case "server_error":
                return "Something went wrong on our end. Please try again."
            default:
                return "Couldn't connect. Please try again."
            }
        }
    }
}

@MainActor
final class ProviderConnectFlow: NSObject, ASWebAuthenticationPresentationContextProviding {
    static let shared = ProviderConnectFlow()

    private struct AuthorizeResponse: Decodable {
        let authorizeUrl: String
        let expiresAt: Date
    }

    /// Runs the full connect flow and reports how it ended.
    ///
    /// The authorize URL is fetched over the normal API client, which attaches the
    /// bearer token; `ASWebAuthenticationSession` cannot set headers itself, which
    /// is why the backend hands back JSON rather than a redirect.
    func connect(providerSlug: String) async throws -> ConnectResult {
        let resp: AuthorizeResponse = try await APIClient.shared.post(
            "/v1/providers/\(providerSlug)/authorize"
        )
        guard let start = URL(string: resp.authorizeUrl) else {
            throw APIError.http(0, "bad authorize url")
        }

        let callback: URL
        do {
            callback = try await openWebAuth(start: start, callbackScheme: "coach")
        } catch let error as ASWebAuthenticationSessionError where error.code == .canceledLogin {
            return .cancelled
        }

        let result = parse(callback, fallbackSlug: providerSlug)
        if case .connected(let slug) = result {
            // Posted here rather than relying on `onOpenURL`: the callback URL is
            // consumed by ASWebAuthenticationSession and never reaches the app's
            // URL handler. DeepLinkRouter still posts this for the cases where the
            // OS does route the scheme to us.
            NotificationCenter.default.post(
                name: .providerConnected, object: nil, userInfo: ["provider": slug]
            )
        }
        return result
    }

    private func parse(_ url: URL, fallbackSlug: String) -> ConnectResult {
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        func value(_ name: String) -> String? {
            items.first { $0.name == name }?.value
        }
        let slug = value("provider") ?? fallbackSlug
        guard value("status") == "ok" else {
            let reason = value("reason") ?? "unknown"
            return reason == "denied" ? .denied : .failed(reason: reason)
        }
        return .connected(slug)
    }

    private func openWebAuth(start: URL, callbackScheme: String) async throws -> URL {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<URL, Error>) in
            let session = ASWebAuthenticationSession(
                url: start,
                callbackURLScheme: callbackScheme
            ) { url, error in
                if let error {
                    cont.resume(throwing: error)
                } else if let url {
                    cont.resume(returning: url)
                } else {
                    cont.resume(throwing: APIError.http(0, "no callback url"))
                }
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
