import SwiftUI

/// Negotiating today's session with Will.
///
/// Not a general chat. One exchange, one visible result: you say what is in the
/// way, Will answers, and the session on screen changes or it does not. The
/// chat tab exists for open questions; this exists to move a specific plan.
struct AdjustSheet: View {
    let prescription: String
    let items: [SessionItem]
    /// Called with the revised brief so the dashboard and the session sheet
    /// stop disagreeing about what today is.
    let onAdjusted: (TodayBrief) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var draft = ""
    @State private var reply: String?
    @State private var revised: [SessionItem]?
    @State private var changed = false
    @State private var sending = false
    @State private var error: String?
    @FocusState private var focused: Bool

    /// Removes the blank-field problem. Nobody knows what they are allowed to
    /// ask an AI coach until they see three examples of it.
    private let suggestions = [
        "No barbell today",
        "Only have 30 minutes",
        "Shoulder's sore",
        "Somewhere I can train outside",
    ]

    var body: some View {
        NavigationStack {
            ScreenBackground {
                ScrollView {
                    VStack(alignment: .leading, spacing: Theme.Metric.stack) {
                        current
                        if let reply { response(reply) }
                        if reply == nil { chips }
                        composer
                        if let error {
                            Text(error)
                                .font(Theme.Font.caption)
                                .foregroundStyle(Theme.Palette.bad)
                        }
                    }
                    .padding(.horizontal, Theme.Metric.gutter)
                    .padding(.bottom, 40)
                }
                .scrollDismissesKeyboard(.interactively)
            }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button(changed ? "Done" : "Cancel") { dismiss() }
                        .tint(changed ? Theme.Palette.accent : Theme.Palette.textDim)
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { focused = false }.tint(Theme.Palette.accent)
                }
            }
            .toolbarBackground(Theme.Palette.bg, for: .navigationBar)
        }
        .preferredColorScheme(.dark)
    }

    private var current: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel(changed ? "Updated session" : "Today")
            Text(prescription)
                .font(Theme.Font.headline(20))
                .lineSpacing(4)
                .foregroundStyle(Theme.Palette.text)
                .fixedSize(horizontal: false, vertical: true)

            ForEach(revised ?? items) { item in
                HStack(alignment: .top, spacing: 10) {
                    Circle()
                        .fill(Theme.Palette.textDim.opacity(0.5))
                        .frame(width: 4, height: 4)
                        .padding(.top, 8)
                    Text(item.text)
                        .font(Theme.Font.body)
                        .foregroundStyle(Theme.Palette.textDim)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
            }
        }
        .padding(.top, 8)
    }

    private func response(_ text: String) -> some View {
        Panel(accent: changed ? Theme.Palette.accent : nil) {
            VStack(alignment: .leading, spacing: 8) {
                SectionLabel("Will")
                Text(text)
                    .font(Theme.Font.headline(17))
                    .lineSpacing(4)
                    .foregroundStyle(Theme.Palette.text)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var chips: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(suggestions, id: \.self) { s in
                Button {
                    draft = s
                    Task { await send() }
                } label: {
                    Text(s)
                        .font(Theme.Font.body)
                        .foregroundStyle(Theme.Palette.text)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 11)
                        .background(Theme.Palette.surfaceHigh)
                        .clipShape(Capsule())
                }
                .buttonStyle(PressStyle())
            }
        }
    }

    private var composer: some View {
        VStack(spacing: 12) {
            TextField("What's in the way?", text: $draft, axis: .vertical)
                .lineLimit(1...4)
                .font(Theme.Font.body)
                .foregroundStyle(Theme.Palette.text)
                .focused($focused)
                .padding(16)
                .background(Theme.Palette.surface)
                .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius, style: .continuous))

            ActionButton(title: sending ? "Asking Will" : "Ask Will") {
                Task { await send() }
            }
            .disabled(sending || draft.trimmingCharacters(in: .whitespaces).isEmpty)
            .opacity(sending || draft.trimmingCharacters(in: .whitespaces).isEmpty ? 0.5 : 1)
        }
    }

    private func send() async {
        let message = draft.trimmingCharacters(in: .whitespaces)
        guard !message.isEmpty, !sending else { return }
        sending = true
        focused = false
        defer { sending = false }
        error = nil

        struct Response: Decodable {
            let reply: String
            let changed: Bool
            let brief: TodayBrief
        }
        do {
            let r: Response = try await APIClient.shared.post(
                "/v1/dashboard/today/adjust", body: ["message": message]
            )
            reply = r.reply
            draft = ""
            if r.changed {
                changed = true
                revised = r.brief.sessionItems
            }
            // Reported even when unchanged: Will may have answered a question,
            // and the caller still wants the fresh brief.
            onAdjusted(r.brief)
        } catch {
            self.error = error.localizedDescription
        }
    }
}
