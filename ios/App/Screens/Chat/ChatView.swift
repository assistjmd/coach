import SwiftUI

struct ChatMessageItem: Identifiable {
    let id = UUID()
    let role: String  // "user" | "assistant"
    var content: String
}

struct ChatView: View {
    @State private var messages: [ChatMessageItem] = []
    @State private var draft: String = ""
    @State private var streaming: Bool = false
    @FocusState private var inputFocused: Bool

    var body: some View {
        ScreenBackground {
            VStack(spacing: 0) {
                header
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 20) {
                            if messages.isEmpty { starters }
                            ForEach(messages) { msg in
                                MessageBlock(message: msg).id(msg.id)
                            }
                            if streaming, messages.last?.content.isEmpty == true {
                                ThinkingIndicator()
                            }
                        }
                        .padding(.horizontal, Theme.Metric.gutter)
                        .padding(.vertical, 20)
                    }
                    // Swiping the transcript puts the keyboard away, which is
                    // what most people reach for first.
                    .scrollDismissesKeyboard(.interactively)
                    .onChange(of: messages.count) { _, _ in
                        if let last = messages.last {
                            withAnimation(.easeOut(duration: 0.25)) {
                                proxy.scrollTo(last.id, anchor: .bottom)
                            }
                        }
                    }
                }
                composer
            }
        }
        // Belt and braces: tapping anywhere outside the field also dismisses.
        // Without both of these the keyboard was a dead end — there was no
        // gesture and no button that put it away.
        .contentShape(Rectangle())
        .onTapGesture { inputFocused = false }
        .toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") { inputFocused = false }
                    .font(Theme.Font.button)
                    .tint(Theme.Palette.accent)
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            SectionLabel("Ask")
            Text("COACH")
                .font(.system(size: 34, weight: .black))
                .tracking(-0.5)
                .foregroundStyle(Theme.Palette.text)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Theme.Metric.gutter)
        .padding(.top, 12)
        .padding(.bottom, 10)
    }

    /// Empty-state starters. Cheaper than an explainer, and they teach what the
    /// coach can actually answer.
    private var starters: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel("Try asking")
            ForEach([
                "Should I train today?",
                "Why is my HRV down this week?",
                "What should this week look like?",
            ], id: \.self) { text in
                Button {
                    draft = text
                    inputFocused = true
                } label: {
                    HStack {
                        Text(text)
                            .font(Theme.Font.body)
                            .foregroundStyle(Theme.Palette.text)
                        Spacer()
                        Text("→")
                            .font(Theme.Font.body)
                            .foregroundStyle(Theme.Palette.accent)
                    }
                    .padding(.vertical, 14)
                    .padding(.horizontal, 16)
                    .background(Theme.Palette.surface)
                    .overlay(Rectangle().stroke(Theme.Palette.line, lineWidth: 1))
                    .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
                }
                .buttonStyle(PressStyle())
            }
        }
        .padding(.bottom, 8)
    }

    private var composer: some View {
        VStack(spacing: 0) {
            Rectangle().fill(Theme.Palette.line).frame(height: 1)
            HStack(alignment: .bottom, spacing: 12) {
                TextField("", text: $draft, axis: .vertical)
                    .placeholder(when: draft.isEmpty) {
                        Text("Ask about your training")
                            .foregroundStyle(Theme.Palette.textDim)
                    }
                    .focused($inputFocused)
                    .font(Theme.Font.body)
                    .foregroundStyle(Theme.Palette.text)
                    .lineLimit(1...5)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 12)
                    .background(Theme.Palette.surface)
                    .overlay(Rectangle().stroke(Theme.Palette.line, lineWidth: 1))
                    .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))

                Button {
                    Task { await send() }
                } label: {
                    Text("↑")
                        .font(.system(size: 20, weight: .black))
                        .frame(width: 46, height: 46)
                        .foregroundStyle(canSend ? Theme.Palette.bg : Theme.Palette.textDim)
                        .background(canSend ? Theme.Palette.accent : Theme.Palette.surface)
                        .clipShape(RoundedRectangle(cornerRadius: Theme.Metric.radius))
                }
                .buttonStyle(PressStyle())
                .disabled(!canSend)
            }
            .padding(.horizontal, Theme.Metric.gutter)
            .padding(.vertical, 12)
        }
        .background(Theme.Palette.bg)
    }

    private var canSend: Bool {
        !draft.trimmingCharacters(in: .whitespaces).isEmpty && !streaming
    }

    private func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        messages.append(.init(role: "user", content: text))
        draft = ""
        streaming = true
        let assistantIndex = messages.count
        messages.append(.init(role: "assistant", content: ""))

        do {
            for try await chunk in ChatStream.stream(message: text) {
                messages[assistantIndex].content += chunk
            }
        } catch APIError.http(429, let detail) {
            // Quota. The server's sentence is already user-facing, so show it
            // rather than wrapping it in an error frame.
            messages[assistantIndex].content = detail
        } catch APIError.http(_, let detail) where !detail.isEmpty {
            messages[assistantIndex].content = detail
        } catch {
            messages[assistantIndex].content =
                "Couldn't reach the coach. \(error.localizedDescription)"
        }
        streaming = false
    }
}

/// Messages are not bubbles. The coach speaks in full-width prose under a label;
/// only the athlete's own questions are inset and marked with a rule.
private struct MessageBlock: View {
    let message: ChatMessageItem

    var body: some View {
        if message.role == "user" {
            HStack(alignment: .top, spacing: 12) {
                Rectangle()
                    .fill(Theme.Palette.textDim)
                    .frame(width: 2)
                Text(message.content)
                    .font(Theme.Font.body)
                    .foregroundStyle(Theme.Palette.textDim)
            }
            .fixedSize(horizontal: false, vertical: true)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                SectionLabel("Will")
                Text(message.content)
                    .font(.system(size: 17, weight: .regular))
                    .lineSpacing(4)
                    .foregroundStyle(Theme.Palette.text)
                    .textSelection(.enabled)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct ThinkingIndicator: View {
    @State private var lit = false
    var body: some View {
        HStack(spacing: 5) {
            ForEach(0..<3, id: \.self) { i in
                Circle()
                    .fill(Theme.Palette.accent)
                    .frame(width: 6, height: 6)
                    .opacity(lit ? 1 : 0.25)
                    .animation(
                        .easeInOut(duration: 0.5)
                            .repeatForever()
                            .delay(Double(i) * 0.15),
                        value: lit
                    )
            }
        }
        .onAppear { lit = true }
    }
}

extension View {
    /// SwiftUI has no styleable placeholder on `TextField` — the built-in one is
    /// locked to the system secondary colour, which is nearly invisible here.
    func placeholder<P: View>(
        when shouldShow: Bool,
        alignment: Alignment = .leading,
        @ViewBuilder placeholder: () -> P
    ) -> some View {
        ZStack(alignment: alignment) {
            placeholder().opacity(shouldShow ? 1 : 0).padding(.horizontal, 14)
            self
        }
    }
}
