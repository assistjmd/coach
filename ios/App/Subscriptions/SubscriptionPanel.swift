import SwiftUI

/// Subscription state, and the way in to the paywall.
///
/// Present from the first day rather than appearing when the trial runs out.
/// Someone who has already decided should not have to wait two weeks to pay,
/// and a countdown that only shows up at the end reads as a trap.
struct SubscriptionPanel: View {
    @ObservedObject private var store = Store.shared
    @State private var showingPaywall = false

    var body: some View {
        Panel(accent: store.isSubscribed ? Theme.Palette.accent : nil) {
            VStack(alignment: .leading, spacing: 12) {
                SectionLabel(store.isSubscribed ? "Subscription" : "Coach Will")
                Text(headline)
                    .font(Theme.Font.headline(19))
                    .foregroundStyle(Theme.Palette.text)
                    .fixedSize(horizontal: false, vertical: true)

                if !store.isSubscribed {
                    ActionButton(title: "See plans") { showingPaywall = true }
                    Text(detail)
                        .font(.system(size: 12))
                        .foregroundStyle(Theme.Palette.textDim)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    // Cancellation goes through Apple, not through us. Hiding
                    // that would be the sort of thing that earns a one-star
                    // review, and Apple requires the path to be findable.
                    Link("Manage in Apple ID settings",
                         destination: URL(string: "https://apps.apple.com/account/subscriptions")!)
                        .font(Theme.Font.caption)
                        .tint(Theme.Palette.accent)
                    // Still reachable while subscribed: someone paying should
                    // be able to see what they are paying for, and the App
                    // Store review screenshot has to come from somewhere.
                    Button("See plans") { showingPaywall = true }
                        .font(Theme.Font.caption)
                        .tint(Theme.Palette.textDim)
                }
            }
        }
        .sheet(isPresented: $showingPaywall) {
            NavigationStack { PaywallView() }
        }
        .task { await store.refresh() }
    }

    private var headline: String {
        if store.isSubscribed { return "You're subscribed. Thank you." }
        if !store.enforced { return "Free while I'm still building this." }
        switch store.trialDaysLeft {
        case 0: return "Your trial has ended."
        case 1: return "Last day of your trial."
        default: return "\(store.trialDaysLeft) days left in your trial."
        }
    }

    private var detail: String {
        store.enforced
            ? "Everything stays open until then. Your history is yours either way."
            : "Subscriptions aren't switched on yet. Nothing to pay for now."
    }
}
