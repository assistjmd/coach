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
    /// Which field is being edited, keyed by item id and kind. Held here rather
    /// than inside each field so the keyboard toolbar can dismiss it.
    @FocusState private var editing: String?

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
                                editing: $editing,
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
                // decimalPad and numberPad have no return key, so without this
                // the keyboard covers the Save button with no way to dismiss it.
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { editing = nil }
                        .font(Theme.Font.button)
                        .tint(Theme.Palette.accent)
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
        var payload: [String: [String: Any]] = [:]
        for (id, actual) in actuals {
            var fields: [String: Any] = [:]
            if let s = actual.sets { fields["sets"] = s }
            if let r = actual.reps { fields["reps"] = r }
            if let w = actual.weight {
                fields["weight"] = w
                fields["weight_unit"] = actual.weightUnit ?? ItemRow.defaultUnit
            }
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

private extension ItemRow {
    /// The unit last used, defaulting to pounds.
    ///
    /// Locale detection was deciding this and getting it wrong — a phone can be
    /// set to a metric region by someone who has lifted in pounds their whole
    /// life. Pounds is the better default for this app's users, and the unit is
    /// tappable, so a wrong guess costs one tap rather than every entry.
    static var defaultUnit: String {
        UserDefaults.standard.string(forKey: "weight.unit") ?? "lb"
    }

    static func rememberUnit(_ unit: String) {
        UserDefaults.standard.set(unit, forKey: "weight.unit")
    }
}

private struct ItemRow: View {
    let item: SessionItem
    let done: Bool
    let actual: ItemActual?
    @FocusState.Binding var editing: String?
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
                    HStack(spacing: 10) {
                        if item.sets != nil {
                            Counter(
                                label: "Sets", value: sets,
                                fieldID: "\(item.id).sets", editing: $editing
                            ) { new in
                                onActual(ItemActual(
                                    sets: new, reps: actual?.reps,
                                    weight: actual?.weight, weightUnit: actual?.weightUnit
                                ))
                            }
                        }
                        if item.reps != nil {
                            Counter(
                                label: "Reps", value: reps,
                                fieldID: "\(item.id).reps", editing: $editing
                            ) { new in
                                onActual(ItemActual(
                                    sets: actual?.sets, reps: new,
                                    weight: actual?.weight, weightUnit: actual?.weightUnit
                                ))
                            }
                        }
                        // Only where a load makes sense. A 20 minute easy run
                        // has no weight, and a field asking for one invites a
                        // number that means nothing.
                        if item.sets != nil {
                            WeightField(
                                weight: actual?.weight,
                                unit: actual?.weightUnit ?? Self.defaultUnit,
                                fieldID: "\(item.id).weight",
                                editing: $editing
                            ) { value, unit in
                                onActual(ItemActual(
                                    sets: actual?.sets, reps: actual?.reps,
                                    weight: value, weightUnit: unit
                                ))
                            }
                        }
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
    let fieldID: String
    @FocusState.Binding var editing: String?
    let onChange: (Int) -> Void

    @State private var showingField = false
    @State private var text = ""

    private var focused: Bool { editing == fieldID }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(label.uppercased())
                .font(Theme.Font.sectionLabel)
                .tracking(0.8)
                .foregroundStyle(Theme.Palette.textDim)
                .lineLimit(1)
            HStack(spacing: 4) {
                stepButton("−") { onChange(max(0, value - 1)) }
                if showingField {
                    TextField("", text: $text)
                        .keyboardType(.numberPad)
                        .focused($editing, equals: fieldID)
                        .font(Theme.Font.stat(19))
                        .foregroundStyle(Theme.Palette.text)
                        .frame(minWidth: 30)
                        .multilineTextAlignment(.center)
                        .onSubmit(commit)
                        // numberPad has no return key either, so the sheet's
                        // Done button clears `editing` and this notices.
                        .onChange(of: editing) { _, current in
                            if current != fieldID { commit() }
                        }
                } else {
                    Text("\(value)")
                        .font(Theme.Font.stat(19))
                        .foregroundStyle(Theme.Palette.text)
                        .frame(minWidth: 30)
                        .contentShape(Rectangle())
                        .onTapGesture {
                            text = "\(value)"
                            showingField = true
                            editing = fieldID
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
        showingField = false
    }

    private func stepButton(_ symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(symbol)
                .font(.system(size: 17, weight: .black))
                // 30 rather than 34: three groups plus a changed-marker have to
                // share 400pt, and at the old size the row ran off both edges.
                .frame(width: 30, height: 30)
                .foregroundStyle(Theme.Palette.text)
                .background(Theme.Palette.surfaceHigh)
                .overlay(Rectangle().stroke(Theme.Palette.line, lineWidth: 1))
                .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
        }
        .buttonStyle(PressStyle())
    }
}

/// Load for a lift, with the unit chosen from the phone's locale.
///
/// Free text rather than a stepper: weights jump in fives and tens and go to
/// three figures, and tapping "+" forty times to record 185 is not logging, it
/// is punishment.
private struct WeightField: View {
    let weight: Double?
    let unit: String
    let fieldID: String
    @FocusState.Binding var editing: String?
    let onChange: (Double?, String) -> Void

    @State private var text = ""

    private var focused: Bool { editing == fieldID }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("WEIGHT")
                .font(Theme.Font.sectionLabel)
                .tracking(1.2)
                .foregroundStyle(Theme.Palette.textDim)
            HStack(spacing: 6) {
                TextField("—", text: $text)
                    .keyboardType(.decimalPad)
                    .focused($editing, equals: fieldID)
                    .font(Theme.Font.stat(20))
                    .foregroundStyle(Theme.Palette.text)
                    .frame(minWidth: 44)
                    .multilineTextAlignment(.center)
                    .padding(.vertical, 6)
                    .background(Theme.Palette.surfaceHigh)
                    .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
                    // On every keystroke, not on blur. Tapping Save with the
                    // keyboard still up ran save() before the field ever lost
                    // focus, so a typed weight was read back as nil and the
                    // number silently vanished.
                    .onChange(of: text) { _, _ in commit() }
                Button {
                    let next = unit == "lb" ? "kg" : "lb"
                    ItemRow.rememberUnit(next)
                    onChange(parsed(), next)
                } label: {
                    Text(unit)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Theme.Palette.accent)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 4)
                        .background(Theme.Palette.surfaceHigh)
                        .clipShape(Capsule())
                }
                .buttonStyle(PressStyle())
            }
        }
        .task { syncFromStored() }
        // The sheet populates its actuals after the first render, so a task
        // that runs once sees nil and never corrects itself. Following the
        // value instead — but never while the field has focus, or it would
        // rewrite what someone is mid-way through typing.
        .onChange(of: weight) { _, _ in syncFromStored() }
    }

    private func syncFromStored() {
        guard !focused else { return }
        guard let weight else {
            text = ""
            return
        }
        text = weight == weight.rounded() ? String(Int(weight)) : String(weight)
    }

    /// The field's value, or nil when it is empty or nonsense.
    ///
    /// Bounded rather than merely parsed: a stray keypress turning 225 into
    /// 2255 is a number the coach would then try to progress from.
    private func parsed() -> Double? {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard let value = Double(trimmed), value > 0, value < 2000 else { return nil }
        return value
    }

    private func commit() {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        if trimmed.isEmpty {
            // Cleared on purpose. Distinct from unparseable, which is left
            // alone so a half-typed "2" does not wipe an existing weight.
            onChange(nil, unit)
            return
        }
        guard let value = parsed() else { return }
        onChange(value, unit)
    }
}
