import SwiftUI

struct DashboardView: View {
    @EnvironmentObject var health: HealthKitManager
    @State private var brief: TodayBrief?
    @State private var state: LoadState = .loading
    /// Drives the entrance animation. Panels settle in rather than snapping,
    /// which is most of what makes the UI feel physical.
    @State private var appeared = false

    enum LoadState: Equatable {
        case loading
        /// The server has nothing to write a brief from yet. This is an expected
        /// state, not a failure — the API signals it with a 404 — so it gets a
        /// designed screen instead of the raw error string.
        case empty
        case failed(String)
        case loaded
    }

    var body: some View {
        NavigationStack {
            ScreenBackground {
                ScrollView {
                    VStack(alignment: .leading, spacing: Theme.Metric.stack) {
                        header
                        switch state {
                        case .loading: loading
                        case .empty: emptyState
                        case .failed(let message): failure(message)
                        case .loaded: if let brief { briefBody(brief) }
                        }
                    }
                    .padding(.horizontal, Theme.Metric.gutter)
                    .padding(.bottom, 40)
                }
                .refreshable { await load() }
            }
            .toolbar(.hidden, for: .navigationBar)
        }
        .task {
            // Deliberately does not request HealthKit authorization. Onboarding
            // owns the first prompt — the system sheet is one-shot, so firing it
            // here would burn the single chance to ask before the explainer.
            await health.syncOnce()
            await load()
            withAnimation(.spring(response: 0.5, dampingFraction: 0.85)) {
                appeared = true
            }
        }
    }

    // MARK: Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            SectionLabel(Date.now.formatted(.dateTime.weekday(.wide).day().month(.wide)))
            Text("TODAY")
                .font(.system(size: 40, weight: .black))
                .tracking(-0.5)
                .foregroundStyle(Theme.Palette.text)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 12)
        .padding(.bottom, 6)
    }

    // MARK: States

    private var loading: some View {
        Panel {
            HStack(spacing: 12) {
                ProgressView().tint(Theme.Palette.accent)
                Text("Reading your last 14 days")
                    .font(Theme.Font.caption)
                    .foregroundStyle(Theme.Palette.textDim)
            }
        }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: Theme.Metric.stack) {
            Panel(accent: Theme.Palette.caution) {
                VStack(alignment: .leading, spacing: 10) {
                    SectionLabel("No brief yet")
                    Text("I need something to read before I can call your day.")
                        .font(Theme.Font.bodyStrong)
                        .foregroundStyle(Theme.Palette.text)
                    Text("Connect a tracker and you'll have a brief on the next sync.")
                        .font(Theme.Font.body)
                        .foregroundStyle(Theme.Palette.textDim)
                }
            }
            NavigationLink {
                ConnectionsView()
            } label: {
                Text("CONNECT A TRACKER")
                    .font(Theme.Font.button)
                    .tracking(1.4)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 17)
                    .foregroundStyle(Theme.Palette.bg)
                    .background(Theme.Palette.accent)
                    .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
            }
            .buttonStyle(PressStyle())
        }
    }

    private func failure(_ message: String) -> some View {
        Panel(accent: Theme.Palette.bad) {
            VStack(alignment: .leading, spacing: 12) {
                SectionLabel("Couldn't load")
                Text(message)
                    .font(Theme.Font.body)
                    .foregroundStyle(Theme.Palette.textDim)
                ActionButton(title: "Retry", filled: false) {
                    Task { await load() }
                }
            }
        }
    }

    // MARK: Brief

    @ViewBuilder
    private func briefBody(_ b: TodayBrief) -> some View {
        if b.stale {
            Panel(accent: Theme.Palette.caution) {
                Text("This is from \(b.day). I couldn't build a new one yet.")
                    .font(Theme.Font.caption)
                    .foregroundStyle(Theme.Palette.textDim)
            }
        }

        // Primary panel. The accent spine marks what to read first.
        Panel(accent: Theme.Palette.accent) {
            VStack(alignment: .leading, spacing: 10) {
                SectionLabel("Where you are")
                Text(b.readinessSummary)
                    .font(.system(size: 19, weight: .medium))
                    .lineSpacing(3)
                    .foregroundStyle(Theme.Palette.text)
            }
        }
        .offset(y: appeared ? 0 : 12)
        .opacity(appeared ? 1 : 0)

        Panel {
            VStack(alignment: .leading, spacing: 10) {
                SectionLabel("Today's session")
                Text(b.trainingPrescription)
                    .font(Theme.Font.bodyStrong)
                    .lineSpacing(3)
                    .foregroundStyle(Theme.Palette.text)
            }
        }
        .offset(y: appeared ? 0 : 16)
        .opacity(appeared ? 1 : 0)

        if let rationale = b.rationale, !rationale.isEmpty {
            Panel {
                VStack(alignment: .leading, spacing: 8) {
                    SectionLabel("Why")
                    Text(rationale)
                        .font(Theme.Font.body)
                        .foregroundStyle(Theme.Palette.textDim)
                }
            }
            .offset(y: appeared ? 0 : 20)
            .opacity(appeared ? 1 : 0)
        }
    }

    // MARK: Load

    private func load() async {
        do {
            brief = try await APIClient.shared.get("/v1/dashboard/today")
            state = .loaded
        } catch APIError.http(404, _) {
            state = .empty
        } catch {
            state = .failed(error.localizedDescription)
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
