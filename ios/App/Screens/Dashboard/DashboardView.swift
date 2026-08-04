import SwiftUI

struct DashboardView: View {
    @EnvironmentObject var health: HealthKitManager
    @State private var brief: TodayBrief?
    @State private var loadError: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    if let brief {
                        if brief.stale {
                            staleBanner(brief)
                        }
                        readinessCard(brief)
                        prescriptionCard(brief)
                        if let rationale = brief.rationale {
                            rationaleCard(rationale)
                        }
                    } else if let err = loadError {
                        ContentUnavailableView(
                            "No brief yet",
                            systemImage: "moon.zzz",
                            description: Text(err)
                        )
                    } else {
                        ProgressView().padding()
                    }
                }
                .padding()
            }
            .navigationTitle("Today")
            .refreshable { await load() }
            .task {
                if !health.authorized {
                    await health.requestAuthorization()
                }
                await load()
            }
        }
    }

    /// Shown when the server fell back to an older brief because it couldn't
    /// generate one for today. Presenting stale numbers as current would be
    /// worse than saying nothing.
    private func staleBanner(_ b: TodayBrief) -> some View {
        Label("This brief is from \(b.day) — we couldn't build a new one yet.",
              systemImage: "clock.arrow.circlepath")
            .font(.footnote)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func readinessCard(_ b: TodayBrief) -> some View {
        Card(title: "Readiness", systemImage: "heart.text.square") {
            Text(b.readinessSummary)
                .font(.body)
        }
    }

    private func prescriptionCard(_ b: TodayBrief) -> some View {
        Card(title: "Today's training", systemImage: "figure.run") {
            Text(b.trainingPrescription)
                .font(.body)
        }
    }

    private func rationaleCard(_ text: String) -> some View {
        Card(title: "Why", systemImage: "info.circle") {
            Text(text)
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }

    private func load() async {
        do {
            brief = try await APIClient.shared.get("/v1/dashboard/today")
            loadError = nil
        } catch {
            loadError = error.localizedDescription
        }
    }
}

struct TodayBrief: Decodable {
    // `day` stays a String: it is a calendar date, and decoding it as a Date
    // would shift it across timezones.
    let day: String
    let readinessSummary: String
    let trainingPrescription: String
    let rationale: String?
    /// True when the server served an older brief rather than today's.
    let stale: Bool
}

struct Card<Content: View>: View {
    let title: String
    let systemImage: String
    @ViewBuilder var content: () -> Content
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(title, systemImage: systemImage)
                .font(.headline)
                .foregroundStyle(.secondary)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(RoundedRectangle(cornerRadius: 16).fill(Color(.secondarySystemBackground)))
    }
}
