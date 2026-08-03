import SwiftUI

struct ProviderRow: Decodable, Identifiable {
    let slug: String
    let displayName: String
    let connected: Bool
    let lastSyncedAt: Date?
    var id: String { slug }
}

struct MeResponse: Decodable {
    let id: String
    let email: String?
    let displayName: String?
    let providers: [ProviderRow]
}

struct ConnectionsView: View {
    @State private var me: MeResponse?
    @State private var error: String?
    @State private var notice: String?
    @State private var busySlug: String?

    var body: some View {
        NavigationStack {
            List {
                if let me {
                    Section("Connected") {
                        ForEach(me.providers) { p in
                            ProviderTile(
                                p: p,
                                isBusy: busySlug == p.slug,
                                run: { await run(p.slug, $0) }
                            )
                        }
                    }
                } else if let error {
                    Text(error).foregroundStyle(.red)
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("Connections")
            .refreshable { await load() }
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

private struct ProviderTile: View {
    let p: ProviderRow
    let isBusy: Bool
    /// Runs the given work and surfaces its optional message. Returning nil means
    /// "succeeded, say nothing".
    let run: (@escaping () async throws -> String?) async -> Void

    @EnvironmentObject var health: HealthKitManager

    var body: some View {
        HStack {
            VStack(alignment: .leading) {
                Text(p.displayName).font(.headline)
                if let ts = p.lastSyncedAt {
                    Text("Last sync \(ts.formatted(.relative(presentation: .named)))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else if p.connected {
                    Text("Connected").font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("Not connected").font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            if isBusy {
                ProgressView()
            } else {
                actionButton
            }
        }
    }

    @ViewBuilder
    private var actionButton: some View {
        if p.slug == "healthkit" {
            Button(health.authorized ? "Authorized" : "Authorize") {
                Task { await run { await health.requestAuthorization(); return nil } }
            }
            .disabled(health.authorized)
        } else if p.connected {
            Button("Disconnect", role: .destructive) {
                Task {
                    await run {
                        try await APIClient.shared.delete("/v1/providers/\(p.slug)")
                        return nil
                    }
                }
            }
        } else {
            Button("Connect") {
                Task {
                    await run {
                        try await ProviderConnectFlow.shared.connect(providerSlug: p.slug).message
                    }
                }
            }
        }
    }
}
