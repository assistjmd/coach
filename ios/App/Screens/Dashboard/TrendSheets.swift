import SwiftUI

/// Shared loader for both detail sheets.
///
/// Neither needs a new endpoint: `/v1/insights/calendar` already returns a
/// readiness score and a resolved compliance status for every day, which is
/// exactly what these two views are made of. Adding endpoints that re-derive the
/// same numbers would give the home screen and the calendar a way to disagree.
@MainActor
private final class TrendData: ObservableObject {
    @Published var days: [DayEntry] = []
    @Published var error: String?
    @Published var loading = true

    func load(days window: Int) async {
        loading = true
        defer { loading = false }
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = .current
        let end = Date()
        guard let start = Calendar.current.date(byAdding: .day, value: -window, to: end)
        else { return }
        do {
            let r: CalendarResponse = try await APIClient.shared.get(
                "/v1/insights/calendar?start=\(f.string(from: start))&end=\(f.string(from: end))"
            )
            days = r.days
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - Readiness

struct ReadinessTrendSheet: View {
    @StateObject private var data = TrendData()
    @Environment(\.dismiss) private var dismiss
    private let window = 29

    /// Only days that actually have a score. A brief that failed to generate is
    /// a gap, and plotting it as zero would invent a crash that never happened.
    private var scored: [DayEntry] { data.days.filter { $0.readiness != nil } }

    var body: some View {
        SheetScaffold(title: "Readiness", subtitle: "Last 30 days", dismiss: dismiss) {
            if data.loading && scored.isEmpty {
                ProgressView().tint(Theme.Palette.accent)
            } else if scored.count < 2 {
                EmptyNote("Not enough days yet. This fills in as briefs build up.")
            } else {
                summary
                TrendBars(
                    values: scored.map { Double($0.readiness ?? 0) },
                    labels: scored.map(\.day),
                    colour: { Theme.Palette.readiness(Int($0)) }
                )
                recent
            }
            if let e = data.error { EmptyNote(e) }
        }
        .task { await data.load(days: window) }
    }

    private var summary: some View {
        let values = scored.compactMap { $0.readiness }
        let avg = values.reduce(0, +) / max(values.count, 1)
        // Compared against the previous week rather than the whole window: what
        // matters is the direction now, not the mean of a month.
        let recent7 = Array(values.suffix(7))
        let prior7 = Array(values.dropLast(7).suffix(7))
        let delta: Int? = prior7.isEmpty
            ? nil
            : recent7.reduce(0, +) / max(recent7.count, 1)
                - prior7.reduce(0, +) / max(prior7.count, 1)

        return Panel {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    SectionLabel("Average")
                    Text("\(avg)")
                        .font(Theme.Font.stat(30))
                        .foregroundStyle(Theme.Palette.readiness(avg))
                }
                Spacer()
                if let delta {
                    VStack(alignment: .trailing, spacing: 4) {
                        SectionLabel("vs last week")
                        Text(delta == 0 ? "level" : "\(delta > 0 ? "+" : "")\(delta)")
                            .font(Theme.Font.stat(22))
                            .foregroundStyle(
                                abs(delta) < 3
                                    ? Theme.Palette.textDim
                                    : (delta > 0 ? Theme.Palette.accent : Theme.Palette.caution)
                            )
                    }
                }
            }
        }
    }

    private var recent: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel("Day by day")
            ForEach(scored.reversed().prefix(14)) { d in
                HStack {
                    Text(DayEntry.shortLabel(d.day))
                        .font(Theme.Font.body)
                        .foregroundStyle(Theme.Palette.textDim)
                    Spacer()
                    Text("\(d.readiness ?? 0)")
                        .font(Theme.Font.stat(17))
                        .foregroundStyle(Theme.Palette.readiness(d.readiness ?? 0))
                }
            }
        }
    }
}

// MARK: - Consistency

struct ConsistencyDetailSheet: View {
    @StateObject private var data = TrendData()
    @Environment(\.dismiss) private var dismiss
    private let window = 29

    /// Days the coach actually asked for a session. Rest days are excluded here
    /// for the same reason they are excluded from the score — they are not
    /// sessions, and listing them as hits would flatter the picture.
    private var assessed: [DayEntry] {
        data.days.filter { ["complete", "partial", "missed"].contains($0.compliance) }
    }

    var body: some View {
        SheetScaffold(title: "Consistency", subtitle: "Sessions I asked for", dismiss: dismiss) {
            if data.loading && assessed.isEmpty {
                ProgressView().tint(Theme.Palette.accent)
            } else if assessed.isEmpty {
                EmptyNote("No prescribed sessions in this window yet.")
            } else {
                tally
                list
            }
            if let e = data.error { EmptyNote(e) }
        }
        .task { await data.load(days: window) }
    }

    private var tally: some View {
        let done = assessed.filter { $0.compliance == "complete" }.count
        let part = assessed.filter { $0.compliance == "partial" }.count
        let missed = assessed.filter { $0.compliance == "missed" }.count
        return Panel {
            HStack(spacing: 0) {
                tallyItem("Done", done, Theme.Palette.accent)
                tallyItem("Partial", part, Theme.Palette.caution)
                tallyItem("Missed", missed, Theme.Palette.bad)
            }
        }
    }

    private func tallyItem(_ label: String, _ n: Int, _ colour: Color) -> some View {
        VStack(spacing: 4) {
            Text("\(n)")
                .font(Theme.Font.stat(26))
                .foregroundStyle(n == 0 ? Theme.Palette.textDim : colour)
            Text(label)
                .font(Theme.Font.caption)
                .foregroundStyle(Theme.Palette.textDim)
        }
        .frame(maxWidth: .infinity)
    }

    private var list: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel("Session by session")
            ForEach(assessed.reversed()) { d in
                Panel {
                    HStack(alignment: .top, spacing: 12) {
                        Circle()
                            .fill(colour(d.compliance))
                            .frame(width: 8, height: 8)
                            .padding(.top, 6)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(DayEntry.shortLabel(d.day))
                                .font(Theme.Font.bodyStrong)
                                .foregroundStyle(Theme.Palette.text)
                            if let p = d.prescription {
                                Text(p)
                                    .font(Theme.Font.caption)
                                    .foregroundStyle(Theme.Palette.textDim)
                                    .lineLimit(2)
                            }
                        }
                        Spacer(minLength: 0)
                        Text(word(d.compliance))
                            .font(Theme.Font.caption)
                            .foregroundStyle(colour(d.compliance))
                    }
                }
            }
        }
    }

    private func colour(_ status: String) -> Color {
        switch status {
        case "complete": return Theme.Palette.accent
        case "partial": return Theme.Palette.caution
        default: return Theme.Palette.bad
        }
    }

    private func word(_ status: String) -> String {
        switch status {
        case "complete": return "Done"
        case "partial": return "Partial"
        default: return "Missed"
        }
    }
}

// MARK: - Shared pieces

private struct SheetScaffold<Content: View>: View {
    let title: String
    let subtitle: String
    let dismiss: DismissAction
    @ViewBuilder var content: () -> Content

    var body: some View {
        NavigationStack {
            ScreenBackground {
                ScrollView {
                    VStack(alignment: .leading, spacing: Theme.Metric.stack) {
                        VStack(alignment: .leading, spacing: 4) {
                            SectionLabel(subtitle)
                            Text(title)
                                .font(Theme.Font.headline(30))
                                .foregroundStyle(Theme.Palette.text)
                        }
                        .padding(.top, 8)
                        content()
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, Theme.Metric.gutter)
                    .padding(.bottom, 40)
                }
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Close") { dismiss() }.tint(Theme.Palette.textDim)
                }
            }
            .toolbarBackground(Theme.Palette.bg, for: .navigationBar)
        }
        .preferredColorScheme(.dark)
    }
}

private struct EmptyNote: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Panel {
            Text(text)
                .font(Theme.Font.body)
                .foregroundStyle(Theme.Palette.textDim)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// A bar per day. Deliberately unlabelled on the x-axis — thirty date labels at
/// this width are unreadable, and the day-by-day list underneath carries them.
private struct TrendBars: View {
    let values: [Double]
    let labels: [String]
    let colour: (Double) -> Color

    var body: some View {
        GeometryReader { geo in
            let maximum = max(values.max() ?? 1, 1)
            let gap: CGFloat = 3
            let width = max(
                (geo.size.width - gap * CGFloat(max(values.count - 1, 0)))
                    / CGFloat(max(values.count, 1)),
                2
            )
            HStack(alignment: .bottom, spacing: gap) {
                ForEach(Array(values.enumerated()), id: \.offset) { _, v in
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .fill(colour(v))
                        .frame(
                            width: width,
                            // Floor so a very low score is still visible rather
                            // than collapsing to nothing.
                            height: max(geo.size.height * CGFloat(v / maximum), 3)
                        )
                }
            }
            .frame(height: geo.size.height, alignment: .bottom)
        }
        .frame(height: 120)
    }
}
