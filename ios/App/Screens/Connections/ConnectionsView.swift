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
    @EnvironmentObject var health: HealthKitManager

    var body: some View {
        NavigationStack {
            List {
                if let me {
                    Section("Connected") {
                        ForEach(me.providers) { p in
                            ProviderTile(p: p)
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
        }
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
            actionButton
        }
    }

    @ViewBuilder
    private var actionButton: some View {
        if p.slug == "healthkit" {
            Button(health.authorized ? "Authorized" : "Authorize") {
                Task { await health.requestAuthorization() }
            }
            .disabled(health.authorized)
        } else if p.connected {
            Button("Disconnect", role: .destructive) {
                Task {
                    try? await APIClient.shared.delete("/v1/providers/\(p.slug)")
                }
            }
        } else {
            Button("Connect") {
                Task {
                    try? await ProviderConnectFlow.shared.connect(providerSlug: p.slug)
                }
            }
        }
    }
}
