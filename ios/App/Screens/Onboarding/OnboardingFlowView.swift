import SwiftUI

/// Post-sign-in onboarding: state a goal, prime HealthKit, connect a tracker.
///
/// This exists because the coach was previously advising against a goal the user
/// never stated — every new account silently defaulted to "adaptive", and the
/// only way to change it was a Profile tab nothing pointed at.
@MainActor
final class OnboardingModel: ObservableObject {
    enum Step: Hashable {
        case sport, targetDate, details, health, providers
    }

    @Published var draft = GoalBody()
    @Published var path: [Step] = []
    @Published var isSaving = false
    @Published var error: String?

    /// Race is the only kind that needs a date, so the step is skipped entirely
    /// for the others rather than shown and ignored.
    private var stepsAfterSport: [Step] {
        draft.kind == .race ? [.targetDate, .details, .health, .providers]
                            : [.details, .health, .providers]
    }

    func choose(_ kind: GoalKind) {
        draft.kind = kind
        if kind != .race { draft.targetDate = nil }
        path = [.sport]
    }

    func advance(from step: Step) {
        let remaining = stepsAfterSport
        guard let index = remaining.firstIndex(of: step) else {
            // Coming from `.sport`, which is not in the list.
            if let next = remaining.first { path.append(next) }
            return
        }
        if index + 1 < remaining.count { path.append(remaining[index + 1]) }
    }

    /// Persist the goal before the HealthKit step, so a user who abandons at the
    /// permission sheet still has the thing they came to set.
    func saveGoal() async -> Bool {
        isSaving = true
        defer { isSaving = false }
        do {
            let _: GoalBody = try await APIClient.shared.put("/v1/goal", body: draft.dictionary())
            error = nil
            return true
        } catch {
            self.error = error.localizedDescription
            return false
        }
    }

    func complete(session: SessionStore) async {
        isSaving = true
        defer { isSaving = false }
        do {
            // Timezone is captured here because this is the only moment the
            // device reliably tells us. Nothing else ever writes it, and the
            // brief schedule and prompt context both depend on it.
            let _: MeResponse = try await APIClient.shared.post(
                "/v1/me/onboarding/complete",
                body: ["timezone": TimeZone.current.identifier]
            )
            session.finishOnboarding()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct OnboardingFlowView: View {
    @EnvironmentObject var session: SessionStore
    @EnvironmentObject var health: HealthKitManager
    @StateObject private var model = OnboardingModel()

    var body: some View {
        NavigationStack(path: $model.path) {
            GoalKindStep(model: model)
                .navigationDestination(for: OnboardingModel.Step.self) { step in
                    switch step {
                    case .sport: SportStep(model: model)
                    case .targetDate: TargetDateStep(model: model)
                    case .details: DetailsStep(model: model)
                    case .health: HealthPrimerStep(model: model, health: health)
                    case .providers: ProvidersStep(model: model, session: session)
                    }
                }
        }
        .alert("Something went wrong", isPresented: .constant(model.error != nil)) {
            Button("OK") { model.error = nil }
        } message: {
            Text(model.error ?? "")
        }
    }
}

// MARK: - Steps

private struct StepScaffold<Content: View>: View {
    let title: String
    let subtitle: String?
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.title2.bold())
            if let subtitle {
                Text(subtitle).font(.subheadline).foregroundStyle(.secondary)
            }
            content()
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
    }
}

private struct GoalKindStep: View {
    @ObservedObject var model: OnboardingModel

    var body: some View {
        StepScaffold(title: "What are you training for?",
                     subtitle: "This shapes every plan Coach writes for you.") {
            VStack(spacing: 12) {
                ForEach(GoalKind.allCases) { kind in
                    Button { model.choose(kind) } label: {
                        HStack(spacing: 14) {
                            Image(systemName: kind.symbol)
                                .font(.title3)
                                .frame(width: 32)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(kind.title).font(.headline)
                                Text(kind.subtitle)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.footnote)
                                .foregroundStyle(.tertiary)
                        }
                        .padding()
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(
                            RoundedRectangle(cornerRadius: 14)
                                .fill(Color(.secondarySystemBackground))
                        )
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.top, 12)
        }
        .navigationTitle("Your goal")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct SportStep: View {
    @ObservedObject var model: OnboardingModel

    private let columns = [GridItem(.adaptive(minimum: 110), spacing: 12)]

    var body: some View {
        StepScaffold(title: "Primary sport?", subtitle: "You can log anything — this just sets the focus.") {
            ScrollView {
                LazyVGrid(columns: columns, spacing: 12) {
                    ForEach(Sport.allCases) { sport in
                        Button {
                            model.draft.sport = sport
                            model.advance(from: .sport)
                        } label: {
                            VStack(spacing: 6) {
                                Image(systemName: sport.symbol).font(.title3)
                                Text(sport.title).font(.caption).multilineTextAlignment(.center)
                            }
                            .frame(maxWidth: .infinity, minHeight: 78)
                            .background(
                                RoundedRectangle(cornerRadius: 14)
                                    .fill(model.draft.sport == sport
                                          ? Color.accentColor.opacity(0.18)
                                          : Color(.secondarySystemBackground))
                            )
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.top, 12)
            }
        }
        .navigationTitle("Sport")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct TargetDateStep: View {
    @ObservedObject var model: OnboardingModel
    @State private var date = Date().addingTimeInterval(60 * 60 * 24 * 90)

    var body: some View {
        StepScaffold(title: "When's the event?",
                     subtitle: "Coach works backwards from this to plan your build.") {
            DatePicker("Race day", selection: $date, in: Date()..., displayedComponents: .date)
                .datePickerStyle(.graphical)
                .padding(.top, 12)

            Button("Continue") {
                // Stored as a calendar date string, never a Date — see GoalBody.
                model.draft.targetDate = CalendarDate.string(from: date)
                model.advance(from: .targetDate)
            }
            .buttonStyle(.borderedProminent)
            .frame(maxWidth: .infinity)
        }
        .navigationTitle("Race day")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct DetailsStep: View {
    @ObservedObject var model: OnboardingModel
    @FocusState private var focused: Bool

    var body: some View {
        StepScaffold(title: "Anything else Coach should know?",
                     subtitle: "Optional. Target times, injuries, how much you can train.") {
            TextField(
                "e.g. sub-3:30 marathon, coming back from a calf strain",
                text: Binding(
                    get: { model.draft.description ?? "" },
                    set: { model.draft.description = $0.isEmpty ? nil : $0 }
                ),
                axis: .vertical
            )
            .lineLimit(3...6)
            .textFieldStyle(.roundedBorder)
            .focused($focused)
            .padding(.top, 12)

            Button {
                focused = false
                Task {
                    if await model.saveGoal() { model.advance(from: .details) }
                }
            } label: {
                if model.isSaving { ProgressView() } else { Text("Continue") }
            }
            .buttonStyle(.borderedProminent)
            .frame(maxWidth: .infinity)
            .disabled(model.isSaving)
        }
        .navigationTitle("Details")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct HealthPrimerStep: View {
    @ObservedObject var model: OnboardingModel
    @ObservedObject var health: HealthKitManager

    var body: some View {
        StepScaffold(title: "Connect Apple Health",
                     subtitle: "Coach reads your data — it never writes to Health.") {
            VStack(alignment: .leading, spacing: 14) {
                Row("bed.double", "Sleep stages and duration")
                Row("waveform.path.ecg", "Heart rate and HRV")
                Row("figure.run", "Workouts and VO₂ max")
            }
            .padding(.vertical, 20)

            Text("You'll see the system permission sheet next. You can change this anytime in Settings.")
                .font(.footnote)
                .foregroundStyle(.secondary)

            Spacer()

            // A pre-permission explainer, because the system sheet is one-shot:
            // deny it and the only route back is Settings. "Not now" skips
            // without spending that single chance.
            Button("Allow access") {
                Task {
                    await health.requestAuthorization()
                    model.advance(from: .health)
                }
            }
            .buttonStyle(.borderedProminent)
            .frame(maxWidth: .infinity)

            Button("Not now") { model.advance(from: .health) }
                .frame(maxWidth: .infinity)
        }
        .navigationTitle("Apple Health")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func Row(_ symbol: String, _ text: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol).frame(width: 26).foregroundStyle(.tint)
            Text(text)
        }
    }
}

private struct ProvidersStep: View {
    @ObservedObject var model: OnboardingModel
    let session: SessionStore
    @State private var me: MeResponse?
    @State private var connecting: String?
    @State private var notice: String?

    /// Apple Health is handled by the previous step; OAuth providers only here.
    private var connectable: [ProviderRow] {
        (me?.providers ?? []).filter { $0.slug != "healthkit" && !$0.connected }
    }

    var body: some View {
        StepScaffold(title: "Connect a tracker",
                     subtitle: "Optional — you can add these later from the Connect tab.") {
            VStack(spacing: 12) {
                ForEach(connectable) { p in
                    Button {
                        Task { await connect(p.slug) }
                    } label: {
                        HStack {
                            Text(p.displayName).font(.headline)
                            Spacer()
                            if connecting == p.slug {
                                ProgressView()
                            } else {
                                Image(systemName: "plus.circle")
                            }
                        }
                        .padding()
                        .frame(maxWidth: .infinity)
                        .background(
                            RoundedRectangle(cornerRadius: 14)
                                .fill(Color(.secondarySystemBackground))
                        )
                    }
                    .buttonStyle(.plain)
                    .disabled(connecting != nil)
                }
            }
            .padding(.top, 12)

            Spacer()

            Button {
                Task { await model.complete(session: session) }
            } label: {
                if model.isSaving { ProgressView() } else { Text("Finish") }
            }
            .buttonStyle(.borderedProminent)
            .frame(maxWidth: .infinity)
            .disabled(model.isSaving)
        }
        .navigationTitle("Trackers")
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(model.isSaving)
        .task { me = try? await APIClient.shared.get("/v1/me") }
        .alert("Connection", isPresented: .constant(notice != nil)) {
            Button("OK") { notice = nil }
        } message: { Text(notice ?? "") }
    }

    private func connect(_ slug: String) async {
        connecting = slug
        defer { connecting = nil }
        do {
            notice = try await ProviderConnectFlow.shared.connect(providerSlug: slug).message
        } catch {
            notice = error.localizedDescription
        }
        me = try? await APIClient.shared.get("/v1/me")
    }
}
