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
    @State private var showingReadiness = false
    @State private var showingConsistency = false

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
                    prescription: b.trainingPrescription,
                    items: b.sessionItems,
                    initialCompleted: Array(ticked),
                    initialActuals: actuals
                ) { completed, logged in
                    ticked = Set(completed)
                    actuals = logged
                    Task { await refreshNudges() }
                } onAdjusted: { updated in
                    brief = updated
                    ticked = Set(updated.completedItems)
                    actuals = updated.itemActuals
                }
            }
        }
        .sheet(isPresented: $showingReadiness) { ReadinessTrendSheet() }
        .sheet(isPresented: $showingConsistency) { ConsistencyDetailSheet() }
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
            Button { showingReadiness = true } label: {
                VStack(spacing: 18) {
                    ReadinessRing(score: score)
                    // First sentence only. The full read is a tap away — on the
                    // screen you glance at before you are properly awake, three
                    // lines of prose under the number is three lines nobody
                    // finishes.
                    Text(Self.headline(b.readinessSummary))
                        .font(Theme.Font.headline(20))
                        .multilineTextAlignment(.center)
                        .foregroundStyle(Theme.Palette.text)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity)
                .contentShape(Rectangle())
            }
            .buttonStyle(PressStyle())
            .padding(.top, 12)
            .padding(.bottom, 8)
            .offset(y: appeared ? 0 : 12)
            .opacity(appeared ? 1 : 0)
        } else {
            // Older briefs predate the score. The prose still stands on its own.
            Panel(accent: Theme.Palette.accent) {
                Text(Self.headline(b.readinessSummary))
                    .font(Theme.Font.headline(20))
                    .foregroundStyle(Theme.Palette.text)
            }
            .offset(y: appeared ? 0 : 12)
            .opacity(appeared ? 1 : 0)
        }

        Button { showingConsistency = true } label: {
            ConsistencyCard(
                score: b.consistency,
                streak: b.consistencyStreak ?? 0,
                assessed: b.consistencyAssessed ?? 0
            )
        }
        .buttonStyle(PressStyle())
        .offset(y: appeared ? 0 : 14)
        .opacity(appeared ? 1 : 0)

        Button { if !b.sessionItems.isEmpty { showingSession = true } } label: {
            Panel(accent: Theme.Palette.accent) {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(spacing: 8) {
                        SectionLabel("Today's session")
                        if b.adjusted == true {
                            Text("Adjusted")
                                .font(Theme.Font.caption)
                                .foregroundStyle(Theme.Palette.accent)
                        }
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
                    Text(Self.headline(b.trainingPrescription))
                        .font(Theme.Font.headline(19))
                        .multilineTextAlignment(.leading)
                        .foregroundStyle(Theme.Palette.text)

                    if !b.sessionItems.isEmpty {
                        // The primary action on the screen, sized like it.
                        HStack(spacing: 8) {
                            Text("Open the session")
                                .font(.system(size: 17, weight: .semibold))
                            Image(systemName: "arrow.right")
                                .font(.system(size: 14, weight: .semibold))
                        }
                        .foregroundStyle(Theme.Palette.accent)
                        .padding(.top, 4)
                    }
                }
            }
        }
        .buttonStyle(PressStyle())
        .disabled(b.sessionItems.isEmpty)
        .offset(y: appeared ? 0 : 16)
        .opacity(appeared ? 1 : 0)

    }

    /// The first sentence of a paragraph.
    ///
    /// Today is a glance screen. The coach writes two or three sentences and all
    /// of them are worth reading, but not at 7am with one eye open — the rest is
    /// behind the readiness tap and the session sheet.
    static func headline(_ text: String) -> String {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let end = trimmed.firstIndex(where: { ".!?".contains($0) }) else {
            return trimmed
        }
        let first = String(trimmed[...end])
        // A very short opener ("Recovered.") is a lead-in, not the whole point,
        // so keep the sentence after it too.
        guard first.count < 24 else { return first }
        let rest = trimmed[trimmed.index(after: end)...]
            .trimmingCharacters(in: .whitespaces)
        guard let secondEnd = rest.firstIndex(where: { ".!?".contains($0) }) else {
            return first
        }
        return first + " " + String(rest[...secondEnd])
    }

    /// Re-arm the behaviour reminders from what today actually looks like.
    ///
    /// Done on every load rather than once: ticking the last item off at
    /// lunchtime should remove the evening reminder, not leave it to nag about
    /// work already finished.
    private func refreshNudges() async {
        guard let b = brief else { return }
        await MorningNudge.shared.refreshBehaviour(
            sessionItems: b.sessionItems.count,
            completedItems: ticked.count,
            streak: b.consistencyStreak ?? 0,
            hasBrief: !b.stale
        )
    }

    // MARK: Load

    private func load() async {
        do {
            brief = try await APIClient.shared.get("/v1/dashboard/today")
            ticked = Set(brief?.completedItems ?? [])
            actuals = brief?.itemActuals ?? [:]
            state = .loaded
            await refreshNudges()
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
    /// Share of prescribed sessions actually done. Nil until there are enough
    /// scored days for a percentage to mean anything.
    let consistency: Int?
    let consistencyStreak: Int?
    let consistencyAssessed: Int?
    /// True when the athlete talked Will into changing today's session.
    let adjusted: Bool?
    /// True when the server served an older brief rather than today's.
    let stale: Bool
    let sessionItems: [SessionItem]
    let completedItems: [String]
    let itemActuals: [String: ItemActual]
}
