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

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 12) {
                            ForEach(messages) { msg in
                                MessageBubble(message: msg).id(msg.id)
                            }
                        }
                        .padding()
                    }
                    .onChange(of: messages.count) { _, _ in
                        if let last = messages.last { proxy.scrollTo(last.id, anchor: .bottom) }
                    }
                }
                Divider()
                HStack {
                    TextField("Ask Coach…", text: $draft, axis: .vertical)
                        .lineLimit(1...4)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(RoundedRectangle(cornerRadius: 16).fill(Color(.secondarySystemBackground)))
                    Button {
                        Task { await send() }
                    } label: {
                        Image(systemName: "arrow.up.circle.fill").font(.system(size: 28))
                    }
                    .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty || streaming)
                }
                .padding()
            }
            .navigationTitle("Coach")
        }
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
        } catch {
            messages[assistantIndex].content = "Error: \(error.localizedDescription)"
        }
        streaming = false
    }
}

private struct MessageBubble: View {
    let message: ChatMessageItem
    var body: some View {
        HStack {
            if message.role == "user" { Spacer(minLength: 40) }
            Text(message.content)
                .padding(12)
                .background(
                    RoundedRectangle(cornerRadius: 16)
                        .fill(message.role == "user" ? Color.accentColor : Color(.secondarySystemBackground))
                )
                .foregroundStyle(message.role == "user" ? Color.white : Color.primary)
            if message.role == "assistant" { Spacer(minLength: 40) }
        }
    }
}
