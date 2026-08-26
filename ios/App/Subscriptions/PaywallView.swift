import StoreKit
import SwiftUI

/// The one screen that has to justify the price.
///
/// It leads with the athlete's own fortnight — sessions completed, workouts
/// read, whether readiness moved — because a feature list is the pitch they
/// already accepted two weeks ago. When there is nothing honest to say, because
/// they connected nothing and trained nothing, it falls back to plain copy
/// rather than inventing a story they would see straight through.
struct PaywallView: View {
    /// Blocking after the trial, dismissible when opened voluntarily.
    var blocking: Bool = false

    @ObservedObject private var store = Store.shared
    @Environment(\.dismiss) private var dismiss
    @State private var selected: String = Store.yearlyID

    var body: some View {
        ScreenBackground {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.Metric.stack) {
                    headline
                    if !store.pitch.lines.isEmpty { theirNumbers } else { whatItDoes }
                    plans
                    subscribeButton
                    footer
                }
                .padding(.horizontal, Theme.Metric.gutter)
                .padding(.bottom, 40)
            }
        }
        .toolbar {
            if !blocking {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Close") { dismiss() }.tint(Theme.Palette.textDim)
                }
            }
        }
        .toolbarBackground(Theme.Palette.bg, for: .navigationBar)
        .preferredColorScheme(.dark)
        .alert("Subscription", isPresented: .constant(store.error != nil)) {
            Button("OK") { store.error = nil }
        } message: {
            Text(store.error ?? "")
        }
        .task { await store.refresh() }
    }

    private var headline: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel(blocking ? "Your trial has ended" : "Coach Will")
            Text(blocking ? "Keep training with Will." : "Keep going.")
                .font(Theme.Font.headline(30))
                .foregroundStyle(Theme.Palette.text)
        }
        .padding(.top, 12)
    }

    /// Their fortnight, in their numbers.
    private var theirNumbers: some View {
        Panel(accent: Theme.Palette.accent) {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(Array(store.pitch.lines.enumerated()), id: \.offset) { _, line in
                    HStack(alignment: .top, spacing: 10) {
                        Circle()
                            .fill(Theme.Palette.accent)
                            .frame(width: 5, height: 5)
                            .padding(.top, 8)
                        Text(line)
                            .font(Theme.Font.headline(17))
                            .foregroundStyle(Theme.Palette.text)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                    }
                }
            }
        }
    }

    /// Only for someone whose trial produced no story worth telling.
    private var whatItDoes: some View {
        Panel {
            VStack(alignment: .leading, spacing: 10) {
                ForEach([
                    "A readiness read and one specific session, every morning.",
                    "Change the session by telling me what's in the way.",
                    "Your whole history, and how closely you followed it.",
                ], id: \.self) { line in
                    Text(line)
                        .font(Theme.Font.body)
                        .foregroundStyle(Theme.Palette.textDim)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var plans: some View {
        VStack(spacing: 10) {
            if let y = store.yearly {
                PlanRow(
                    title: "Yearly",
                    price: y.displayPrice,
                    detail: store.yearlyPerMonth.map { "\($0) a month" } ?? "",
                    badge: store.yearlySavingPercent.map { "Save \($0)%" },
                    selected: selected == Store.yearlyID
                ) { selected = Store.yearlyID }
            }
            if let m = store.monthly {
                PlanRow(
                    title: "Monthly",
                    price: m.displayPrice,
                    detail: "billed every month",
                    badge: nil,
                    selected: selected == Store.monthlyID
                ) { selected = Store.monthlyID }
            }
            if store.products.isEmpty {
                Panel {
                    Text("Couldn't reach the App Store. Pull down to try again.")
                        .font(Theme.Font.caption)
                        .foregroundStyle(Theme.Palette.textDim)
                }
            }
        }
    }

    private var subscribeButton: some View {
        ActionButton(title: store.purchasing ? "One moment" : "Subscribe") {
            guard let product = store.products.first(where: { $0.id == selected })
            else { return }
            Task {
                await store.purchase(product)
                if store.isSubscribed && !blocking { dismiss() }
            }
        }
        .disabled(store.purchasing || store.products.isEmpty)
        .opacity(store.purchasing || store.products.isEmpty ? 0.5 : 1)
    }

    private var footer: some View {
        VStack(spacing: 14) {
            // Apple requires a restore path for anything selling a
            // subscription, and it is genuinely needed: the entitlement belongs
            // to the Apple ID, not the phone.
            Button("Restore purchases") { Task { await store.restore() } }
                .font(Theme.Font.caption)
                .tint(Theme.Palette.textDim)

            Text("Renews automatically until cancelled. Cancel any time in your "
                 + "Apple ID settings.")
                .font(.system(size: 11))
                .foregroundStyle(Theme.Palette.textDim)
                .multilineTextAlignment(.center)

            HStack(spacing: 18) {
                Link("Terms", destination: legal("terms"))
                Link("Privacy", destination: legal("privacy"))
            }
            .font(.system(size: 11))
            .tint(Theme.Palette.textDim)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 6)
    }

    private func legal(_ slug: String) -> URL {
        URL(string: APIClient.shared.baseURL.absoluteString + "/legal/" + slug)!
    }
}

private struct PlanRow: View {
    let title: String
    let price: String
    let detail: String
    let badge: String?
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(alignment: .center, spacing: 14) {
                ZStack {
                    Circle()
                        .stroke(selected ? Theme.Palette.accent : Theme.Palette.line,
                                lineWidth: 1.5)
                        .frame(width: 22, height: 22)
                    if selected {
                        Circle().fill(Theme.Palette.accent).frame(width: 12, height: 12)
                    }
                }
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 8) {
                        Text(title)
                            .font(Theme.Font.bodyStrong)
                            .foregroundStyle(Theme.Palette.text)
                        if let badge {
                            Text(badge)
                                .font(.system(size: 10, weight: .heavy))
                                .padding(.horizontal, 7)
                                .padding(.vertical, 3)
                                .foregroundStyle(Theme.Palette.bg)
                                .background(Theme.Palette.accent)
                                .clipShape(Capsule())
                        }
                    }
                    Text(detail)
                        .font(Theme.Font.caption)
                        .foregroundStyle(Theme.Palette.textDim)
                }
                Spacer(minLength: 0)
                Text(price)
                    .font(Theme.Font.stat(19))
                    .foregroundStyle(Theme.Palette.text)
            }
            .padding(Theme.Metric.panelPadding)
            .background(Theme.Palette.surface)
            .overlay(
                RoundedRectangle(cornerRadius: Theme.Metric.radius, style: .continuous)
                    .stroke(selected ? Theme.Palette.accent : Color.clear, lineWidth: 1.5)
            )
            .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(PressStyle())
    }
}
