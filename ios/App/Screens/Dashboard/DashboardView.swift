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
    let day: String
    let readinessSummary: String
    let trainingPrescription: String
    let rationale: String?
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
