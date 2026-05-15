import SwiftUI

struct ProfileView: View {
    @EnvironmentObject var session: SessionStore
    @State private var goal: GoalBody = GoalBody(kind: "adaptive", sport: nil, targetDate: nil, description: nil)
    @State private var loaded: Bool = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Goal") {
                    Picker("Type", selection: $goal.kind) {
                        Text("Adaptive (no fixed goal)").tag("adaptive")
                        Text("Race").tag("race")
                        Text("Improvement").tag("improvement")
                    }
                    if goal.kind == "race" {
                        TextField("Sport (running, cycling, ...)", text: Binding(
                            get: { goal.sport ?? "" },
                            set: { goal.sport = $0.isEmpty ? nil : $0 }
                        ))
                        DatePicker(
                            "Target date",
                            selection: Binding(
                                get: { goal.targetDate ?? Date().addingTimeInterval(60*60*24*60) },
                                set: { goal.targetDate = $0 }
                            ),
                            displayedComponents: .date
                        )
                    }
                    TextField("Notes (e.g. FTP 240W, target 3:30 marathon)", text: Binding(
                        get: { goal.description ?? "" },
                        set: { goal.description = $0.isEmpty ? nil : $0 }
                    ), axis: .vertical)
                    Button("Save goal") {
                        Task { try? await APIClient.shared.put("/v1/goal", body: goal.dictionary()) as EmptyResp }
                    }
                }

                Section {
                    Button("Sign out", role: .destructive) { session.signOut() }
                }
            }
            .navigationTitle("Profile")
            .task {
                if !loaded {
                    if let g: GoalBody = try? await APIClient.shared.get("/v1/goal") {
                        goal = g
                    }
                    loaded = true
                }
            }
        }
    }
}

struct GoalBody: Codable {
    var kind: String
    var sport: String?
    var targetDate: Date?
    var description: String?

    func dictionary() -> [String: Any] {
        var d: [String: Any] = ["kind": kind]
        if let s = sport { d["sport"] = s }
        if let t = targetDate { d["target_date"] = ISO8601DateFormatter().string(from: t) }
        if let desc = description { d["description"] = desc }
        return d
    }
}

private struct EmptyResp: Decodable {}
