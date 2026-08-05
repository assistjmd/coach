import SwiftUI

/// Shape of `GET /v1/me/consent`. At file scope because Swift cannot nest a
/// type inside a generic function.
private struct ConsentStatusResponse: Decodable {
    let upToDate: Bool
}

/// Blocks the app until the current legal documents have been accepted.
///
/// Wraps both the onboarding and ready phases rather than sitting inside
/// onboarding: when the documents change, existing users have to accept the new
/// version too, and they never pass through onboarding again.
///
/// Fails open. If the check itself errors — offline, server down — the app is
/// shown rather than locking someone out of their own training data over a
/// network blip. The gate re-checks on next launch.
struct ConsentGate<Content: View>: View {
    @ViewBuilder var content: () -> Content

    @State private var state: GateState = .checking

    private enum GateState: Equatable {
        case checking
        case needsConsent
        case passed
    }

    var body: some View {
        Group {
            switch state {
            case .checking:
                ScreenBackground {
                    ProgressView().tint(Theme.Palette.accent)
                }
            case .needsConsent:
                ConsentStep { state = .passed }
            case .passed:
                content()
            }
        }
        .task { await check() }
    }

    private func check() async {
        do {
            let status: ConsentStatusResponse = try await APIClient.shared.get("/v1/me/consent")
            state = status.upToDate ? .passed : .needsConsent
        } catch {
            state = .passed
        }
    }
}
