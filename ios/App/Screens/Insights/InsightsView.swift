import SwiftUI

// MARK: - Wire types

struct WorkoutSummary: Decodable, Identifiable {
    let sport: String?
    let durationS: Int?
    let distanceM: Double?
    let avgHr: Int?
    let source: String
    var id: String { "\(source)-\(sport ?? "")-\(durationS ?? 0)-\(distanceM ?? 0)" }
}

struct DayEntry: Decodable, Identifiable {
    let day: String
    let load: Double
    /// "trimp" when every session had heart rate, "duration" when any was
    /// estimated from sport and time alone.
    let loadMethod: String
    let readiness: Int?
    let prescription: String?
    let sessionPlanned: Bool?
    let compliance: String
    let corrected: Bool
    let workouts: [WorkoutSummary]

    var id: String { day }
    var estimated: Bool { loadMethod != "trimp" }
    var date: Date? { DayEntry.parser.date(from: day) }

    private static let parser: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = TimeZone(secondsFromGMT: 0)
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
}

struct CalendarResponse: Decodable {
    let start: String
    let end: String
    let days: [DayEntry]
    let loadTrendPct: Double?
}

// MARK: - Screen

struct InsightsView: View {
    @State private var response: CalendarResponse?
    @State private var error: String?
    @State private var selected: DayEntry?
    /// Days back from today. Four weeks reads as a training block without
    /// shrinking the bars to the point of meaninglessness.
    private let window = 27

    var body: some View {
        NavigationStack {
            ScreenBackground {
                ScrollView {
                    VStack(alignment: .leading, spacing: Theme.Metric.stack) {
                        header
                        if let response {
                            trend(response)
                            chart(response.days)
                            legend
                            recent(response.days)
                        } else if let error {
                            Panel(accent: Theme.Palette.bad) {
                                VStack(alignment: .leading, spacing: 10) {
                                    SectionLabel("Couldn't load")
                                    Text(error)
                                        .font(Theme.Font.body)
                                        .foregroundStyle(Theme.Palette.textDim)
                                    ActionButton(title: "Retry", filled: false) {
                                        Task { await load() }
                                    }
                                }
                            }
                        } else {
                            Panel {
                                HStack(spacing: 12) {
                                    ProgressView().tint(Theme.Palette.accent)
                                    Text("Building your history")
                                        .font(Theme.Font.caption)
                                        .foregroundStyle(Theme.Palette.textDim)
                                }
                            }
                        }
                    }
                    .padding(.horizontal, Theme.Metric.gutter)
                    .padding(.bottom, 40)
                }
                .refreshable { await load() }
            }
            .toolbar(.hidden, for: .navigationBar)
        }
        .task { await load() }
        .sheet(item: $selected) { day in
            DayDetailSheet(day: day) { updated in
                apply(updated)
                selected = nil
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            SectionLabel("Last four weeks")
            Text("HISTORY")
                .font(.system(size: 34, weight: .black))
                .tracking(-0.5)
                .foregroundStyle(Theme.Palette.text)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 12)
        .padding(.bottom, 6)
    }

    private func trend(_ r: CalendarResponse) -> some View {
        Panel(accent: Theme.Palette.accent) {
            VStack(alignment: .leading, spacing: 6) {
                SectionLabel("Load, last 7 days vs the 7 before")
                if let pct = r.loadTrendPct {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(pct >= 0 ? "▲" : "▼")
                            .font(Theme.Font.stat(22))
                            .foregroundStyle(trendColor(pct))
                        Text("\(abs(pct).formatted(.number.precision(.fractionLength(0))))%")
                            .font(Theme.Font.display(38))
                            .foregroundStyle(Theme.Palette.text)
                    }
                    Text(trendNote(pct))
                        .font(Theme.Font.caption)
                        .foregroundStyle(Theme.Palette.textDim)
                } else {
                    Text("Not enough history yet")
                        .font(Theme.Font.bodyStrong)
                        .foregroundStyle(Theme.Palette.textDim)
                }
            }
        }
    }

    /// Sharp jumps in weekly load are the classic injury precursor, so a big
    /// rise is flagged as caution rather than celebrated.
    private func trendColor(_ pct: Double) -> Color {
        pct > 30 ? Theme.Palette.caution : Theme.Palette.accent
    }

    private func trendNote(_ pct: Double) -> String {
        if pct > 30 { return "That's a sharp jump. Worth an easier week." }
        if pct >= 0 { return "Building steadily." }
        return "Backing off — fine if that's deliberate."
    }

    private func chart(_ days: [DayEntry]) -> some View {
        let peak = max(days.map(\.load).max() ?? 1, 1)
        return Panel {
            VStack(alignment: .leading, spacing: 10) {
                SectionLabel("Daily load")
                HStack(alignment: .bottom, spacing: 3) {
                    ForEach(days) { day in
                        LoadBar(day: day, peak: peak)
                            .onTapGesture { selected = day }
                    }
                }
                .frame(height: 96)
            }
        }
    }

    private var legend: some View {
        HStack(spacing: 14) {
            swatch(Theme.Palette.accent, "Ready")
            swatch(Theme.Palette.caution, "Strained")
            swatch(Theme.Palette.line, "No data")
            Spacer()
            Text("Hatched = estimated")
                .font(.system(size: 11))
                .foregroundStyle(Theme.Palette.textDim)
        }
        .padding(.horizontal, 2)
    }

    private func swatch(_ color: Color, _ label: String) -> some View {
        HStack(spacing: 5) {
            Rectangle().fill(color).frame(width: 9, height: 9)
            Text(label)
                .font(.system(size: 11))
                .foregroundStyle(Theme.Palette.textDim)
        }
    }

    /// Reverse-chronological list. The chart shows shape; this says what actually
    /// happened, which is the part worth reading.
    private func recent(_ days: [DayEntry]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel("Day by day")
            ForEach(days.reversed().filter { $0.prescription != nil || $0.load > 0 }) { day in
                Button { selected = day } label: { DayRow(day: day) }
                    .buttonStyle(PressStyle())
            }
        }
        .padding(.top, 4)
    }

    private func apply(_ updated: DayEntry) {
        guard let response else { return }
        let days = response.days.map { $0.day == updated.day ? updated : $0 }
        self.response = CalendarResponse(
            start: response.start, end: response.end,
            days: days, loadTrendPct: response.loadTrendPct
        )
    }

    private func load() async {
        let cal = Calendar.current
        let end = Date()
        guard let start = cal.date(byAdding: .day, value: -window, to: end) else { return }
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(secondsFromGMT: 0)
        do {
            response = try await APIClient.shared.get(
                "/v1/insights/calendar?start=\(f.string(from: start))&end=\(f.string(from: end))"
            )
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - Bar

private struct LoadBar: View {
    let day: DayEntry
    let peak: Double

    var body: some View {
        VStack(spacing: 4) {
            Spacer(minLength: 0)
            ZStack {
                Rectangle().fill(color)
                // Estimated load is hatched so a bar you can't fully trust never
                // looks like one you can.
                if day.estimated && day.load > 0 {
                    Hatch().stroke(Theme.Palette.bg.opacity(0.55), lineWidth: 1.2)
                }
            }
            .frame(height: max(3, CGFloat(day.load / peak) * 84))
            .clipShape(RoundedRectangle(cornerRadius: 1.5))
        }
        .frame(maxWidth: .infinity)
        .contentShape(Rectangle())
    }

    /// Colour is readiness that morning, not the load itself: the useful signal
    /// is whether a hard day landed on a body that was ready for it.
    private var color: Color {
        guard day.load > 0 else { return Theme.Palette.line.opacity(0.5) }
        guard let r = day.readiness else { return Theme.Palette.textDim }
        return Theme.Palette.readiness(r)
    }
}

private struct Hatch: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        var x = -rect.height
        while x < rect.width {
            p.move(to: CGPoint(x: x, y: rect.maxY))
            p.addLine(to: CGPoint(x: x + rect.height, y: rect.minY))
            x += 5
        }
        return p
    }
}

// MARK: - Row

private struct DayRow: View {
    let day: DayEntry

    var body: some View {
        Panel {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text(dayLabel)
                        .font(.system(size: 13, weight: .heavy))
                        .tracking(0.8)
                        .foregroundStyle(Theme.Palette.text)
                    Spacer()
                    ComplianceTag(status: day.compliance, corrected: day.corrected)
                }
                if let p = day.prescription {
                    Text(p)
                        .font(Theme.Font.caption)
                        .foregroundStyle(Theme.Palette.textDim)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                }
                if !day.workouts.isEmpty {
                    Text(day.workouts.map(Self.describe).joined(separator: " · "))
                        .font(Theme.Font.caption)
                        .foregroundStyle(Theme.Palette.text)
                }
            }
        }
    }

    private var dayLabel: String {
        guard let d = day.date else { return day.day }
        return d.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated))
            .uppercased()
    }

    static func describe(_ w: WorkoutSummary) -> String {
        var parts: [String] = []
        if let s = w.sport { parts.append(s.replacingOccurrences(of: "_", with: " ")) }
        if let d = w.durationS, d > 0 { parts.append("\(d / 60)min") }
        if let m = w.distanceM, m > 0 {
            parts.append("\((m / 1000).formatted(.number.precision(.fractionLength(1))))km")
        }
        return parts.joined(separator: " ")
    }
}

struct ComplianceTag: View {
    let status: String
    var corrected: Bool = false

    var body: some View {
        HStack(spacing: 4) {
            Text(label)
                .font(.system(size: 10, weight: .heavy))
                .tracking(0.8)
            if corrected {
                // Marked so the athlete's own edit isn't presented back to them
                // as the app's judgement.
                Text("· YOURS").font(.system(size: 9, weight: .bold))
            }
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 4)
        .foregroundStyle(color)
        .background(color.opacity(0.14))
        .clipShape(RoundedRectangle(cornerRadius: 2))
    }

    private var label: String {
        switch status {
        case "complete": return "DONE"
        case "partial": return "PARTIAL"
        case "missed": return "MISSED"
        case "rest": return "REST"
        default: return "—"
        }
    }

    private var color: Color {
        switch status {
        case "complete": return Theme.Palette.accent
        case "partial": return Theme.Palette.caution
        case "missed": return Theme.Palette.bad
        case "rest": return Theme.Palette.textDim
        default: return Theme.Palette.textDim
        }
    }
}
