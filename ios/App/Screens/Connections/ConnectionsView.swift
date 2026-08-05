import SwiftUI
import UIKit

struct ConnectionsView: View {
    @State private var me: MeResponse?
    @State private var error: String?
    @State private var notice: String?
    @State private var busySlug: String?

    var body: some View {
        ScreenBackground {
            ScrollView {
                VStack(alignment: .leading, spacing: Theme.Metric.stack) {
                    header
                    if let me {
                        ForEach(me.providers) { p in
                            ProviderRowView(
                                p: p,
                                isBusy: busySlug == p.slug,
                                run: { await run(p.slug, $0) }
                            )
                        }
                    } else if let error {
                        Panel(accent: Theme.Palette.bad) {
                            VStack(alignment: .leading, spacing: 10) {
                                SectionLabel("Couldn't load")
                                Text(error)
                                    .font(Theme.Font.body)
                                    .foregroundStyle(Theme.Palette.textDim)
                                ActionButton(title: "Retry", filled: false) {
                                    Task { await load() }
                                }
                            }
                        }
                    } else {
                        Panel {
                            HStack(spacing: 12) {
                                ProgressView().tint(Theme.Palette.accent)
                                Text("Checking your connections")
                                    .font(Theme.Font.caption)
                                    .foregroundStyle(Theme.Palette.textDim)
                            }
                        }
                    }
                }
                .padding(.horizontal, Theme.Metric.gutter)
                .padding(.bottom, 40)
            }
            .refreshable { await load() }
        }
        .task { await load() }
        .onReceive(NotificationCenter.default.publisher(for: .providerConnected)) { _ in
            Task { await load() }
        }
        .alert("Connection", isPresented: .constant(notice != nil)) {
            Button("OK") { notice = nil }
        } message: {
            Text(notice ?? "")
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            SectionLabel("Your data")
            Text("SOURCES")
                .font(.system(size: 34, weight: .black))
                .tracking(-0.5)
                .foregroundStyle(Theme.Palette.text)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 12)
        .padding(.bottom, 6)
    }

    /// Runs a connect/disconnect action, then always reloads so the row reflects
    /// the server rather than what we hoped happened.
    private func run(_ slug: String, _ action: () async throws -> String?) async {
        busySlug = slug
        defer { busySlug = nil }
        do {
            if let message = try await action() {
                notice = message
            }
        } catch {
            notice = error.localizedDescription
        }
        await load()
    }

    private func load() async {
        do {
            me = try await APIClient.shared.get("/v1/me")
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

private struct ProviderRowView: View {
    let p: ProviderRow
    let isBusy: Bool
    /// Runs the given work and surfaces its optional message. Returning nil means
    /// "succeeded, say nothing".
    let run: (@escaping () async throws -> String?) async -> Void

    @EnvironmentObject var health: HealthKitManager

    var body: some View {
        Panel(accent: isLive ? Theme.Palette.accent : nil) {
            HStack(alignment: .center, spacing: 14) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(p.displayName.uppercased())
                        .font(.system(size: 15, weight: .heavy))
                        .tracking(0.8)
                        .foregroundStyle(Theme.Palette.text)
                    status
                }
                Spacer(minLength: 8)
                if isBusy {
                    ProgressView().tint(Theme.Palette.accent)
                } else {
                    actionControl
                }
            }
        }
    }

    /// Whether this source is actually delivering data, as opposed to merely
    /// being marked connected server-side.
    private var isLive: Bool {
        if p.slug == "healthkit" { return health.hasData }
        return p.connected
    }

    /// Sync state, most-actionable first.
    ///
    /// Order matters: an error outranks a timestamp, because a connection that
    /// synced yesterday and is broken today would otherwise read as healthy.
    /// "Connected, waiting for first sync" is called out separately from
    /// "synced" so the gap after connecting doesn't look like a failure.
    @ViewBuilder
    private var status: some View {
        if p.slug == "healthkit" {
            healthKitStatus
        } else if let err = p.lastSyncError {
            Text(err)
                .font(Theme.Font.caption)
                .foregroundStyle(Theme.Palette.bad)
                .lineLimit(3)
        } else if let ts = p.lastSyncedAt {
            Text("Synced \(ts.formatted(.relative(presentation: .named)))")
                .font(Theme.Font.caption)
                .foregroundStyle(Theme.Palette.textDim)
        } else if p.connected {
            Text("Connected — waiting for first sync")
                .font(Theme.Font.caption)
                .foregroundStyle(Theme.Palette.caution)
        } else {
            Text("Not connected")
                .font(Theme.Font.caption)
                .foregroundStyle(Theme.Palette.textDim)
        }
    }

    /// HealthKit is local, so its state comes from the device rather than the
    /// server. It never reports whether read access was granted — an unauthorized
    /// type simply returns no samples — so "denied" and "genuinely empty" are
    /// indistinguishable here, and the copy says what to check instead of
    /// guessing.
    @ViewBuilder
    private var healthKitStatus: some View {
        if let err = health.lastError {
            Text(err)
                .font(Theme.Font.caption)
                .foregroundStyle(Theme.Palette.bad)
                .lineLimit(3)
        } else if health.hasData, let ts = health.lastSync {
            Text("Synced \(ts.formatted(.relative(presentation: .named)))")
                .font(Theme.Font.caption)
                .foregroundStyle(Theme.Palette.textDim)
        } else if health.didRequestAuthorization {
            Text("No data yet — check Settings › Privacy › Health")
                .font(Theme.Font.caption)
                .foregroundStyle(Theme.Palette.caution)
        } else {
            Text("Not authorized")
                .font(Theme.Font.caption)
                .foregroundStyle(Theme.Palette.textDim)
        }
    }

    @ViewBuilder
    private var actionControl: some View {
        if p.slug == "healthkit" {
            // The server reports HealthKit as "connected" for every user, since it
            // can't see the device's permission state. What it means locally is:
            // have we asked, and has anything actually arrived.
            if health.hasData {
                EmptyView()
            } else if health.didRequestAuthorization {
                // Asked, but nothing came through — most likely denied. HealthKit
                // won't tell us, and re-prompting does nothing, so point at the
                // only place that can fix it.
                SmallButton(title: "Settings") {
                    if let url = URL(string: UIApplication.openSettingsURLString) {
                        UIApplication.shared.open(url)
                    }
                }
            } else {
                SmallButton(title: "Authorize", emphasis: true) {
                    Task { await run { await health.requestAuthorization(); return nil } }
                }
            }
        } else if p.connected {
            SmallButton(title: "Disconnect", destructive: true) {
                Task {
                    await run {
                        try await APIClient.shared.delete("/v1/providers/\(p.slug)")
                        return nil
                    }
                }
            }
        } else {
            SmallButton(title: "Connect", emphasis: true) {
                Task {
                    await run {
                        try await ProviderConnectFlow.shared.connect(providerSlug: p.slug).message
                    }
                }
            }
        }
    }
}

private struct SmallButton: View {
    let title: String
    var emphasis: Bool = false
    var destructive: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title.uppercased())
                .font(.system(size: 12, weight: .heavy))
                .tracking(1.1)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .foregroundStyle(foreground)
                .background(emphasis ? Theme.Palette.accent : Color.clear)
                .overlay(
                    Rectangle().stroke(
                        emphasis ? Color.clear : Theme.Palette.line, lineWidth: 1
                    )
                )
                .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
        }
        .buttonStyle(PressStyle())
    }

    private var foreground: Color {
        if emphasis { return Theme.Palette.bg }
        return destructive ? Theme.Palette.bad : Theme.Palette.textDim
    }
}
