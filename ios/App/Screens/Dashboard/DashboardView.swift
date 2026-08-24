import SwiftUI

struct DashboardView: View {
    @EnvironmentObject var health: HealthKitManager
    @State private var brief: TodayBrief?
    @State private var state: LoadState = .loading
    /// Drives the entrance animation. Panels settle in rather than snapping,
    /// which is most of what makes the UI feel physical.
    @State private var appeared = false
    /// Local tick state so a tap fills the box immediately rather than after a
    /// round trip.
    @State private var ticked: Set<String> = []
    @State private var actuals: [String: ItemActual] = [:]
    @State private var showingSession = false

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
        .sheet(isPresented: $showingSession) {
            if let b = brief {
                SessionSheet(
                    day: b.day,
                    items: b.sessionItems,
                    initialCompleted: Array(ticked),
                    initialActuals: actuals
                ) { completed, logged in
                    ticked = Set(completed)
                    actuals = logged
                }
            }
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

    /// Just the date, quietly. A 40pt "TODAY" competed with the score for the
    /// same job, and the score is the thing worth reading.
    private var header: some View {
        Text(Date.now.formatted(.dateTime.weekday(.wide).day().month(.wide)))
            .font(Theme.Font.body)
            .foregroundStyle(Theme.Palette.textDim)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 8)
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
            Panel {
                Text("This is from \(b.day). I couldn't build a new one yet.")
                    .font(Theme.Font.caption)
                    .foregroundStyle(Theme.Palette.caution)
            }
        }

        // The score, centred and alone. Everything below it is support.
        if let score = b.readinessScore {
            VStack(spacing: 20) {
                ReadinessRing(score: score)
                Text(b.readinessSummary)
                    .font(Theme.Font.headline(20))
                    .lineSpacing(5)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(Theme.Palette.text)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity)
            .padding(.top, 12)
            .padding(.bottom, 8)
            .offset(y: appeared ? 0 : 12)
            .opacity(appeared ? 1 : 0)
        } else {
            // Older briefs predate the score. The prose still stands on its own.
            Panel(accent: Theme.Palette.accent) {
                Text(b.readinessSummary)
                    .font(Theme.Font.headline(20))
                    .lineSpacing(5)
                    .foregroundStyle(Theme.Palette.text)
            }
            .offset(y: appeared ? 0 : 12)
            .opacity(appeared ? 1 : 0)
        }

        Button { if !b.sessionItems.isEmpty { showingSession = true } } label: {
            Panel(accent: Theme.Palette.accent) {
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        SectionLabel("Today's session")
                        Spacer()
                        if !b.sessionItems.isEmpty {
                            Text("\(ticked.count)/\(b.sessionItems.count)")
                                .font(Theme.Font.stat(15))
                                .foregroundStyle(
                                    ticked.count == b.sessionItems.count
                                        ? Theme.Palette.accent : Theme.Palette.textDim
                                )
                        }
                    }
                    Text(b.trainingPrescription)
                        .font(Theme.Font.headline(17))
                        .lineSpacing(4)
                        .multilineTextAlignment(.leading)
                        .foregroundStyle(Theme.Palette.text)

                    if !b.sessionItems.isEmpty {
                        HStack(spacing: 6) {
                            Text("Open the session")
                            Image(systemName: "chevron.right")
                                .font(.system(size: 11, weight: .semibold))
                        }
                        .font(Theme.Font.caption)
                        .foregroundStyle(Theme.Palette.accent)
                    }
                }
            }
        }
        .buttonStyle(PressStyle())
        .disabled(b.sessionItems.isEmpty)
        .offset(y: appeared ? 0 : 16)
        .opacity(appeared ? 1 : 0)

        if let rationale = b.rationale, !rationale.isEmpty {
            Panel {
                VStack(alignment: .leading, spacing: 8) {
                    SectionLabel("Why")
                    Text(rationale)
                        .font(Theme.Font.body)
                        .lineSpacing(3)
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
            ticked = Set(brief?.completedItems ?? [])
            actuals = brief?.itemActuals ?? [:]
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
    /// 0-100. Drives the ring the home screen leads with.
    let readinessScore: Int?
    /// True when the server served an older brief rather than today's.
    let stale: Bool
    let sessionItems: [SessionItem]
    let completedItems: [String]
    let itemActuals: [String: ItemActual]
}
