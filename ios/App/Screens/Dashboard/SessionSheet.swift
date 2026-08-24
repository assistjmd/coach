import SwiftUI

/// Today's session, opened from the Today tab.
///
/// Two things happen here: ticking items off, and recording what was actually
/// done when it differed from the prescription. The second is why the sheet
/// exists — a checkbox can only say done or not done, and "I did four sets of
/// six instead of three of eight" is the thing a coach needs to see next week.
struct SessionSheet: View {
    let day: String
    let prescription: String
    let items: [SessionItem]
    /// Seed state. The sheet owns its edits and reports the final state back.
    let initialCompleted: [String]
    let initialActuals: [String: ItemActual]
    let onSaved: ([String], [String: ItemActual]) -> Void
    /// Called when Will revises the session, so every surface showing today
    /// updates together rather than drifting apart.
    let onAdjusted: (TodayBrief) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var ticked: Set<String> = []
    @State private var actuals: [String: ItemActual] = [:]
    @State private var saving = false
    @State private var error: String?
    @State private var loaded = false
    @State private var adjusting = false

    var body: some View {
        NavigationStack {
            ScreenBackground {
                ScrollView {
                    VStack(alignment: .leading, spacing: Theme.Metric.stack) {
                        headline
                        ForEach(items) { item in
                            ItemRow(
                                item: item,
                                done: ticked.contains(item.id),
                                actual: actuals[item.id],
                                onToggle: { toggle(item.id) },
                                onActual: { actuals[item.id] = $0 }
                            )
                        }
                        if let error {
                            Text(error)
                                .font(Theme.Font.caption)
                                .foregroundStyle(Theme.Palette.bad)
                        }
                        ActionButton(title: saving ? "Saving" : "Save") {
                            Task { await save() }
                        }
                        .disabled(saving)
                        .padding(.top, 4)

                        // Second, and quieter. Changing the session is the
                        // exception; ticking it off is the common path.
                        ActionButton(title: "Change this session", filled: false) {
                            adjusting = true
                        }
                    }
                    .padding(.horizontal, Theme.Metric.gutter)
                    .padding(.bottom, 40)
                }
                .scrollDismissesKeyboard(.interactively)
            }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Close") { dismiss() }.tint(Theme.Palette.textDim)
                }
            }
            .toolbarBackground(Theme.Palette.bg, for: .navigationBar)
        }
        .preferredColorScheme(.dark)
        .sheet(isPresented: $adjusting) {
            AdjustSheet(
                prescription: prescription,
                items: items
            ) { updated in
                onAdjusted(updated)
                // The revised session replaces what this sheet is showing, so
                // close rather than leave a stale list behind it.
                dismiss()
            }
        }
        .task {
            guard !loaded else { return }
            loaded = true
            ticked = Set(initialCompleted)
            actuals = initialActuals
        }
    }

    private var headline: some View {
        VStack(alignment: .leading, spacing: 4) {
            SectionLabel("Today's session")
            Text("\(ticked.count) of \(items.count) done")
                .font(.system(size: 26, weight: .black))
                .foregroundStyle(
                    ticked.count == items.count && !items.isEmpty
                        ? Theme.Palette.accent : Theme.Palette.text
                )
        }
        .padding(.top, 8)
    }

    private func toggle(_ id: String) {
        if ticked.contains(id) { ticked.remove(id) } else { ticked.insert(id) }
    }

    private func save() async {
        saving = true
        defer { saving = false }
        // Only send entries that actually carry a number. An empty object would
        // otherwise be stored as "logged a change" when nothing was changed.
        var payload: [String: [String: Int]] = [:]
        for (id, actual) in actuals {
            var fields: [String: Int] = [:]
            if let s = actual.sets { fields["sets"] = s }
            if let r = actual.reps { fields["reps"] = r }
            if !fields.isEmpty { payload[id] = fields }
        }
        do {
            struct Ignored: Decodable {}
            let _: Ignored = try await APIClient.shared.put(
                "/v1/insights/day/\(day)/log",
                body: ["completed_items": Array(ticked), "item_actuals": payload]
            )
            onSaved(Array(ticked), actuals)
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - Row

private struct ItemRow: View {
    let item: SessionItem
    let done: Bool
    let actual: ItemActual?
    let onToggle: () -> Void
    let onActual: (ItemActual) -> Void

    /// Only countable items get steppers. A 20-minute easy run has no sets, and
    /// showing "0 sets" against it would invite logging a number that means
    /// nothing.
    private var countable: Bool { item.sets != nil || item.reps != nil }

    private var sets: Int { actual?.sets ?? item.sets ?? 0 }
    private var reps: Int { actual?.reps ?? item.reps ?? 0 }
    /// True once the logged numbers differ from what was asked for.
    private var changed: Bool {
        (actual?.sets != nil && actual?.sets != item.sets)
            || (actual?.reps != nil && actual?.reps != item.reps)
    }

    var body: some View {
        Panel(accent: done ? Theme.Palette.accent : nil) {
            VStack(alignment: .leading, spacing: 14) {
                Button(action: onToggle) {
                    HStack(alignment: .top, spacing: 12) {
                        ZStack {
                            Rectangle()
                                .fill(done ? Theme.Palette.accent : Color.clear)
                                .overlay(
                                    Rectangle().stroke(
                                        done ? Theme.Palette.accent : Theme.Palette.line,
                                        lineWidth: 1.5
                                    )
                                )
                                .frame(width: 22, height: 22)
                            if done {
                                Text("✓")
                                    .font(.system(size: 14, weight: .black))
                                    .foregroundStyle(Theme.Palette.bg)
                            }
                        }
                        Text(item.text)
                            .font(Theme.Font.bodyStrong)
                            .multilineTextAlignment(.leading)
                            .foregroundStyle(
                                done ? Theme.Palette.textDim : Theme.Palette.text
                            )
                            .strikethrough(done, color: Theme.Palette.textDim)
                        Spacer(minLength: 0)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)

                if countable {
                    Rectangle().fill(Theme.Palette.line).frame(height: 1)
                    HStack(spacing: 22) {
                        if item.sets != nil {
                            Counter(label: "Sets", value: sets) { new in
                                onActual(ItemActual(sets: new, reps: actual?.reps))
                            }
                        }
                        if item.reps != nil {
                            Counter(label: "Reps", value: reps) { new in
                                onActual(ItemActual(sets: actual?.sets, reps: new))
                            }
                        }
                        Spacer(minLength: 0)
                        if changed {
                            VStack(alignment: .trailing, spacing: 2) {
                                Text("CHANGED")
                                    .font(.system(size: 9, weight: .heavy))
                                    .tracking(0.8)
                                    .foregroundStyle(Theme.Palette.caution)
                                Text("asked \(item.sets ?? 0)×\(item.reps ?? 0)")
                                    .font(.system(size: 10))
                                    .foregroundStyle(Theme.Palette.textDim)
                            }
                        }
                    }
                }
            }
        }
    }
}

/// Stepper with a tappable number. Steppers alone are slow for a jump from 3 to
/// 12, so the value itself is a field.
private struct Counter: View {
    let label: String
    let value: Int
    let onChange: (Int) -> Void

    @State private var editing = false
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(label.uppercased())
                .font(Theme.Font.sectionLabel)
                .tracking(1.2)
                .foregroundStyle(Theme.Palette.textDim)
            HStack(spacing: 8) {
                stepButton("−") { onChange(max(0, value - 1)) }
                if editing {
                    TextField("", text: $text)
                        .keyboardType(.numberPad)
                        .focused($focused)
                        .font(Theme.Font.stat(20))
                        .foregroundStyle(Theme.Palette.text)
                        .frame(width: 44)
                        .multilineTextAlignment(.center)
                        .onSubmit(commit)
                        .onChange(of: focused) { _, isFocused in
                            if !isFocused { commit() }
                        }
                } else {
                    Text("\(value)")
                        .font(Theme.Font.stat(20))
                        .foregroundStyle(Theme.Palette.text)
                        .frame(width: 44)
                        .contentShape(Rectangle())
                        .onTapGesture {
                            text = "\(value)"
                            editing = true
                            focused = true
                        }
                }
                stepButton("+") { onChange(min(200, value + 1)) }
            }
        }
    }

    private func commit() {
        // Ignore anything unparseable rather than resetting to zero, which would
        // silently destroy a number the user meant to keep.
        if let parsed = Int(text), (0...200).contains(parsed) {
            onChange(parsed)
        }
        editing = false
    }

    private func stepButton(_ symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(symbol)
                .font(.system(size: 18, weight: .black))
                .frame(width: 34, height: 34)
                .foregroundStyle(Theme.Palette.text)
                .background(Theme.Palette.surfaceHigh)
                .overlay(Rectangle().stroke(Theme.Palette.line, lineWidth: 1))
                .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
        }
        .buttonStyle(PressStyle())
    }
}
