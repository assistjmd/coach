import Foundation
import StoreKit

/// StoreKit 2, and the app's view of whether this account may use the coach.
///
/// Entitlement is decided by the server, never here. StoreKit can say a purchase
/// happened on this device; only Apple's signature over the transaction, checked
/// server-side, makes that a fact — and the same Apple ID on a second phone, or
/// a refund processed last week, are both things this process cannot see.
///
/// So the flow is: buy through StoreKit, hand the signed transaction to the
/// backend, and take the answer from the backend.
@MainActor
final class Store: ObservableObject {
    static let shared = Store()

    /// Mirrors the server's view. `enforced` is false until the products exist
    /// in App Store Connect, and the UI must not show a paywall it cannot
    /// satisfy.
    @Published private(set) var state: String = "trial"
    @Published private(set) var enforced = false
    @Published private(set) var trialDaysLeft = 0
    @Published private(set) var pitch = Pitch()
    @Published private(set) var products: [Product] = []
    @Published private(set) var purchasing = false
    @Published var error: String?

    /// Yearly first — better value and better retention, so it leads rather
    /// than sitting underneath as an upsell.
    static let yearlyID = "com.trainwithcoach.app.yearly"
    static let monthlyID = "com.trainwithcoach.app.monthly"

    private var updates: Task<Void, Never>?

    struct Pitch: Decodable {
        var lines: [String] = []
        var sessionsCompleted: Int?
        var sessionsAsked: Int?
        var workoutsLogged: Int?
        var readinessChange: Int?
        var daysWithCoach: Int = 0
    }

    struct Status: Decodable {
        let state: String
        let trialDaysLeft: Int
        let enforced: Bool
        let pitch: Pitch
    }

    private init() {
        // Started at launch and never cancelled: Apple delivers renewals,
        // refunds and Ask-to-Buy approvals through this stream, usually while
        // no purchase is in flight.
        updates = Task { [weak self] in
            for await result in Transaction.updates {
                await self?.handle(result)
            }
        }
    }

    var needsPaywall: Bool { enforced && state == "expired" }
    var isSubscribed: Bool { state == "subscribed" }

    var yearly: Product? { products.first { $0.id == Self.yearlyID } }
    var monthly: Product? { products.first { $0.id == Self.monthlyID } }

    /// The yearly price expressed per month, so the comparison is like for like
    /// rather than $99 against $12.99.
    var yearlyPerMonth: String? {
        guard let y = yearly else { return nil }
        return y.priceFormatStyle.format(y.price / 12)
    }

    var yearlySavingPercent: Int? {
        guard let y = yearly, let m = monthly, m.price > 0 else { return nil }
        let full = m.price * 12
        let saved = ((full - y.price) / full) as NSDecimalNumber
        return Int(saved.doubleValue * 100)
    }

    func refresh() async {
        await loadProducts()
        await loadStatus()
    }

    func loadProducts() async {
        // Not surfaced on failure: an empty list already renders as "can't reach
        // the App Store", and this throws on every offline launch.
        products = (try? await Product.products(for: [Self.yearlyID, Self.monthlyID])) ?? []
    }

    func loadStatus() async {
        do {
            let s: Status = try await APIClient.shared.get("/v1/subscriptions/status")
            state = s.state
            enforced = s.enforced
            trialDaysLeft = s.trialDaysLeft
            pitch = s.pitch
        } catch {
            // Leave the last known state alone. Defaulting to expired on a
            // dropped connection would paywall someone who has paid.
        }
    }

    func purchase(_ product: Product) async {
        purchasing = true
        error = nil
        defer { purchasing = false }
        do {
            switch try await product.purchase() {
            case .success(let verification):
                await handle(verification)
            case .userCancelled:
                break
            case .pending:
                // Ask-to-Buy, or a card needing approval. The transaction
                // arrives later through Transaction.updates.
                error = "That's waiting on approval. I'll unlock it as soon as it clears."
            @unknown default:
                break
            }
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Restore on a new device.
    ///
    /// Required by App Store review for anything selling a subscription, and
    /// genuinely needed: the entitlement belongs to the Apple ID, not the phone.
    func restore() async {
        purchasing = true
        defer { purchasing = false }
        for await result in Transaction.currentEntitlements {
            await handle(result)
        }
        await loadStatus()
        if state != "subscribed" {
            error = "No subscription found on this Apple ID."
        }
    }

    private func handle(_ result: VerificationResult<Transaction>) async {
        // `.unverified` is deliberately not dropped here. The server re-verifies
        // the signature against Apple's certificate chain and is what decides;
        // a second, weaker check on the device could only disagree with it.
        let tx: Transaction
        switch result {
        case .verified(let value): tx = value
        case .unverified(let value, _): tx = value
        }
        await send(result.jwsRepresentation)
        // Always finish, or StoreKit replays the transaction on every launch.
        await tx.finish()
    }

    private func send(_ jws: String) async {
        do {
            let s: Status = try await APIClient.shared.post(
                "/v1/subscriptions/verify", body: ["signed_transaction": jws]
            )
            state = s.state
            enforced = s.enforced
            trialDaysLeft = s.trialDaysLeft
            pitch = s.pitch
        } catch {
            self.error = "Purchase went through, but I couldn't confirm it. "
                + "Pull to refresh in a moment."
        }
    }
}
