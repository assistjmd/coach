import SwiftUI

/// "Download my data", as promised by the privacy policy.
///
/// Fetched to a temporary file and handed to the system share sheet rather than
/// displayed. A wall of JSON on screen is not a copy of your data — a file you
/// can mail to yourself, drop in Files, or open in something else is.
struct ExportPanel: View {
    @State private var exporting = false
    @State private var fileURL: URL?
    @State private var error: String?

    var body: some View {
        Panel {
            VStack(alignment: .leading, spacing: 12) {
                SectionLabel("Your data")

                ActionButton(
                    title: exporting ? "Preparing…" : "Download my data",
                    filled: false
                ) {
                    Task { await export() }
                }
                .disabled(exporting)

                Text("A JSON file with everything on your account: workouts, "
                     + "sleep, briefs, journal entries and chat history. Your "
                     + "connections are listed, but never their access keys.")
                    .font(.system(size: 12))
                    .foregroundStyle(Theme.Palette.textDim)
                    .fixedSize(horizontal: false, vertical: true)

                if let error {
                    Text(error)
                        .font(Theme.Font.caption)
                        .foregroundStyle(Theme.Palette.bad)
                }
            }
        }
        .sheet(item: $fileURL) { url in ShareSheet(items: [url]) }
    }

    private func export() async {
        exporting = true
        defer { exporting = false }
        error = nil
        do {
            let data = try await APIClient.shared.getRaw("/v1/me/export")
            // Written into the temporary directory: iOS reclaims it, so a copy
            // of someone's full health history doesn't linger in app storage
            // after they have shared it.
            let stamp = CalendarDate.localString(from: Date())
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("coach-will-export-\(stamp).json")
            try data.write(to: url, options: .atomic)
            fileURL = url
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}
