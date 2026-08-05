import SwiftUI
import UIKit

/// Post-sign-in onboarding: state a goal, prime HealthKit, connect a tracker.
///
/// This exists because the coach was previously advising against a goal the user
/// never stated — every new account silently defaulted to "adaptive", and the
/// only way to change it was a Profile tab nothing pointed at.
///
/// Structure note: this was five pushed screens (kind → sport → date → details →
/// health → trackers). Every answer cost a navigation transition, which made a
/// two-minute setup feel like a form wizard. It is now two: one screen for the
/// goal, one for data sources. The conditional race-date field appears inline
/// rather than as its own step.
@MainActor
final class OnboardingModel: ObservableObject {
    enum Step: Hashable {
        case data
    }

    @Published var draft = GoalBody()
    @Published var path: [Step] = []
    @Published var isSaving = false
    @Published var error: String?

    /// Persist the goal before the data step, so a user who abandons at the
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
            GoalStep(model: model)
                .navigationDestination(for: OnboardingModel.Step.self) { _ in
                    DataStep(model: model, health: health, session: session)
                }
        }
        .preferredColorScheme(.dark)
        .alert("Something went wrong", isPresented: .constant(model.error != nil)) {
            Button("OK") { model.error = nil }
        } message: {
            Text(model.error ?? "")
        }
    }
}

// MARK: - Scaffold

private struct StepScaffold<Content: View, Footer: View>: View {
    let step: Int
    let title: String
    let subtitle: String?
    @ViewBuilder var content: () -> Content
    @ViewBuilder var footer: () -> Footer

    var body: some View {
        ScreenBackground {
            VStack(alignment: .leading, spacing: 0) {
                // Progress is shown as filled bars rather than a page count:
                // it says how far in you are without implying a long form.
                HStack(spacing: 5) {
                    ForEach(1...2, id: \.self) { i in
                        Rectangle()
                            .fill(i <= step ? Theme.Palette.accent : Theme.Palette.line)
                            .frame(height: 3)
                    }
                }
                .padding(.top, 8)
                .padding(.bottom, 26)

                Text(title)
                    .font(.system(size: 28, weight: .black))
                    .tracking(-0.4)
                    .foregroundStyle(Theme.Palette.text)
                if let subtitle {
                    Text(subtitle)
                        .font(Theme.Font.body)
                        .foregroundStyle(Theme.Palette.textDim)
                        .padding(.top, 6)
                }

                ScrollView {
                    content().padding(.top, 22)
                }
                .scrollIndicators(.hidden)
                .scrollDismissesKeyboard(.interactively)

                footer().padding(.top, 12).padding(.bottom, 20)
            }
            .padding(.horizontal, Theme.Metric.gutter)
        }
        .toolbar(.hidden, for: .navigationBar)
        .toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") {
                    // No @FocusState here — the fields own theirs. Resigning
                    // first responder globally is simpler than threading a
                    // binding through the scaffold for one button.
                    UIApplication.shared.sendAction(
                        #selector(UIResponder.resignFirstResponder),
                        to: nil, from: nil, for: nil
                    )
                }
                .font(Theme.Font.button)
                .tint(Theme.Palette.accent)
            }
        }
    }
}

// MARK: - Step 1: the goal

private struct GoalStep: View {
    @ObservedObject var model: OnboardingModel
    @FocusState private var notesFocused: Bool
    @State private var raceDate = Date().addingTimeInterval(60 * 60 * 24 * 90)

    private let sportColumns = [GridItem(.adaptive(minimum: 104), spacing: 8)]

    var body: some View {
        StepScaffold(
            step: 1,
            title: "What are you training for?",
            subtitle: "This shapes every plan Will writes for you."
        ) {
            VStack(alignment: .leading, spacing: 26) {
                block("Focus") {
                    VStack(spacing: 8) {
                        ForEach(GoalKind.allCases) { kind in
                            SelectRow(
                                title: kind.title,
                                detail: kind.subtitle,
                                selected: model.draft.kind == kind
                            ) {
                                model.draft.kind = kind
                                if kind != .race { model.draft.targetDate = nil }
                            }
                        }
                    }
                }

                block("Primary sport") {
                    LazyVGrid(columns: sportColumns, spacing: 8) {
                        ForEach(Sport.allCases) { sport in
                            Chip(
                                title: sport.title,
                                selected: model.draft.sport == sport
                            ) {
                                model.draft.sport = sport
                            }
                        }
                    }
                }

                // Inline rather than its own screen — it only applies to one
                // of the three focus options.
                if model.draft.kind == .race {
                    block("Event date") {
                        DatePicker("", selection: $raceDate, in: Date()...,
                                   displayedComponents: .date)
                            .datePickerStyle(.compact)
                            .labelsHidden()
                            .tint(Theme.Palette.accent)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .transition(.opacity.combined(with: .move(edge: .top)))
                }

                block("Anything else? (optional)") {
                    TextField(
                        "",
                        text: Binding(
                            get: { model.draft.description ?? "" },
                            set: { model.draft.description = $0.isEmpty ? nil : $0 }
                        ),
                        axis: .vertical
                    )
                    .placeholder(when: (model.draft.description ?? "").isEmpty) {
                        Text("Target times, injuries, days you can train")
                            .foregroundStyle(Theme.Palette.textDim)
                    }
                    .focused($notesFocused)
                    .font(Theme.Font.body)
                    .foregroundStyle(Theme.Palette.text)
                    .lineLimit(2...5)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 12)
                    .background(Theme.Palette.surface)
                    .overlay(Rectangle().stroke(Theme.Palette.line, lineWidth: 1))
                    .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
                }
            }
            .animation(.spring(response: 0.35, dampingFraction: 0.85),
                       value: model.draft.kind)
        } footer: {
            ActionButton(title: model.isSaving ? "Saving" : "Continue") {
                notesFocused = false
                if model.draft.kind == .race {
                    model.draft.targetDate = CalendarDate.string(from: raceDate)
                }
                Task {
                    if await model.saveGoal() { model.path = [.data] }
                }
            }
            .disabled(model.isSaving)
            .opacity(model.isSaving ? 0.6 : 1)
        }
    }

    private func block<C: View>(_ label: String, @ViewBuilder content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel(label)
            content()
        }
    }
}

// MARK: - Step 2: data sources

private struct DataStep: View {
    @ObservedObject var model: OnboardingModel
    @ObservedObject var health: HealthKitManager
    let session: SessionStore

    @State private var me: MeResponse?
    @State private var connecting: String?
    @State private var notice: String?

    /// Apple Health is presented separately — it is a device permission, not an
    /// OAuth connection, and it is the one most users should turn on.
    private var connectable: [ProviderRow] {
        (me?.providers ?? []).filter { $0.slug != "healthkit" && !$0.connected }
    }

    var body: some View {
        StepScaffold(
            step: 2,
            title: "Where should I get your data?",
            subtitle: "You can add more later from Sources."
        ) {
            VStack(alignment: .leading, spacing: 22) {
                Panel(accent: health.didRequestAuthorization ? Theme.Palette.accent : nil) {
                    VStack(alignment: .leading, spacing: 12) {
                        SectionLabel("Apple Health")
                        Text("Sleep, heart rate and HRV, workouts and VO₂ max.")
                            .font(Theme.Font.body)
                            .foregroundStyle(Theme.Palette.textDim)
                        Text("Read only. Will never writes to Health.")
                            .font(Theme.Font.caption)
                            .foregroundStyle(Theme.Palette.textDim)

                        if health.didRequestAuthorization {
                            Text("Authorized")
                                .font(Theme.Font.caption)
                                .foregroundStyle(Theme.Palette.accent)
                        } else {
                            // Pre-permission explainer, because the system sheet
                            // is one-shot: deny it and the only route back is
                            // Settings.
                            ActionButton(title: "Allow access") {
                                Task { await health.requestAuthorization() }
                            }
                        }
                    }
                }

                if !connectable.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        SectionLabel("Trackers")
                        ForEach(connectable) { p in
                            SelectRow(
                                title: p.displayName,
                                detail: connecting == p.slug ? "Opening…" : "Tap to connect",
                                selected: false
                            ) {
                                Task { await connect(p.slug) }
                            }
                            .disabled(connecting != nil)
                        }
                    }
                }
            }
        } footer: {
            ActionButton(title: model.isSaving ? "Setting up" : "Start") {
                Task { await model.complete(session: session) }
            }
            .disabled(model.isSaving)
            .opacity(model.isSaving ? 0.6 : 1)
        }
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

// MARK: - Controls

private struct SelectRow: View {
    let title: String
    let detail: String?
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 14) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(title)
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(Theme.Palette.text)
                    if let detail {
                        Text(detail)
                            .font(Theme.Font.caption)
                            .foregroundStyle(Theme.Palette.textDim)
                            .multilineTextAlignment(.leading)
                    }
                }
                Spacer(minLength: 8)
                Rectangle()
                    .fill(selected ? Theme.Palette.accent : Color.clear)
                    .overlay(
                        Rectangle().stroke(
                            selected ? Theme.Palette.accent : Theme.Palette.line,
                            lineWidth: 1.5
                        )
                    )
                    .frame(width: 18, height: 18)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.Palette.surface)
            .overlay(
                Rectangle().stroke(
                    selected ? Theme.Palette.accent : Theme.Palette.line,
                    lineWidth: 1
                )
            )
            .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
        }
        .buttonStyle(PressStyle())
    }
}

private struct Chip: View {
    let title: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 13, weight: .bold))
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .foregroundStyle(selected ? Theme.Palette.bg : Theme.Palette.text)
                .background(selected ? Theme.Palette.accent : Theme.Palette.surface)
                .overlay(
                    Rectangle().stroke(
                        selected ? Color.clear : Theme.Palette.line, lineWidth: 1
                    )
                )
                .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
        }
        .buttonStyle(PressStyle())
    }
}
