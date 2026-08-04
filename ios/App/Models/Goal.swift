import Foundation

/// The user's training goal, shared by onboarding and Profile.
///
/// `targetDate` is a `String` in `yyyy-MM-dd`, not a `Date`, and that is
/// deliberate. A race day is a calendar date, not an instant: as a `Date` it
/// carries a timezone, so a user in UTC-6 who picks March 1 can round-trip to
/// February 28. It also matches what the backend stores — a Postgres `date` —
/// so nothing has to be reinterpreted on either side.
///
/// (This is also what made a saved goal appear to vanish: the write path
/// happened to work because Pydantic accepts `...T00:00:00Z` for a date field,
/// while the read path failed to decode `"2026-08-03"` as an ISO8601 datetime
/// and the error was swallowed.)
struct GoalBody: Codable, Equatable {
    var kind: GoalKind
    var sport: Sport?
    var targetDate: String?
    var description: String?

    init(
        kind: GoalKind = .adaptive,
        sport: Sport? = nil,
        targetDate: String? = nil,
        description: String? = nil
    ) {
        self.kind = kind
        self.sport = sport
        self.targetDate = targetDate
        self.description = description
    }

    /// Wire form for `PUT /v1/goal`. Omits empty fields rather than sending
    /// nulls, so a partially-filled goal doesn't clear values it never set.
    func dictionary() -> [String: Any] {
        var d: [String: Any] = ["kind": kind.rawValue]
        if let sport { d["sport"] = sport.rawValue }
        if let targetDate, !targetDate.isEmpty { d["target_date"] = targetDate }
        if let description, !description.isEmpty { d["description"] = description }
        return d
    }
}

enum GoalKind: String, Codable, CaseIterable, Identifiable {
    case adaptive, race, improvement
    var id: String { rawValue }

    var title: String {
        switch self {
        case .race: return "Train for a race"
        case .improvement: return "Get faster or stronger"
        case .adaptive: return "Just keep me healthy"
        }
    }

    var subtitle: String {
        switch self {
        case .race: return "You have an event on the calendar"
        case .improvement: return "A specific target, no fixed date"
        case .adaptive: return "Coach adapts to how you're recovering"
        }
    }

    var symbol: String {
        switch self {
        case .race: return "flag.checkered"
        case .improvement: return "chart.line.uptrend.xyaxis"
        case .adaptive: return "leaf"
        }
    }
}

/// Mirrors the canonical vocabulary in `backend/app/providers/sports.py`.
/// The backend rejects anything outside this set, so the two must stay in step.
enum Sport: String, Codable, CaseIterable, Identifiable {
    case running, trailRunning = "trail_running"
    case cycling, mtb, indoorCycling = "indoor_cycling"
    case swimming, rowing, strength
    case walking, hiking, yoga
    case skiNordic = "ski_nordic", skiAlpine = "ski_alpine"
    case other

    var id: String { rawValue }

    var title: String {
        switch self {
        case .running: return "Running"
        case .trailRunning: return "Trail running"
        case .cycling: return "Cycling"
        case .mtb: return "Mountain biking"
        case .indoorCycling: return "Indoor cycling"
        case .swimming: return "Swimming"
        case .rowing: return "Rowing"
        case .strength: return "Strength"
        case .walking: return "Walking"
        case .hiking: return "Hiking"
        case .yoga: return "Yoga"
        case .skiNordic: return "Nordic skiing"
        case .skiAlpine: return "Alpine skiing"
        case .other: return "Something else"
        }
    }

    var symbol: String {
        switch self {
        case .running, .trailRunning: return "figure.run"
        case .cycling, .mtb, .indoorCycling: return "bicycle"
        case .swimming: return "figure.pool.swim"
        case .rowing: return "figure.rower"
        case .strength: return "dumbbell"
        case .walking: return "figure.walk"
        case .hiking: return "figure.hiking"
        case .yoga: return "figure.yoga"
        case .skiNordic, .skiAlpine: return "figure.skiing.downhill"
        case .other: return "sparkles"
        }
    }
}

/// Calendar-date conversion, fixed to UTC and a POSIX locale so a device's
/// region settings can never shift the day.
enum CalendarDate {
    static let formatter: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(secondsFromGMT: 0)
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    static func string(from date: Date) -> String { formatter.string(from: date) }
    static func date(from string: String?) -> Date? {
        guard let string else { return nil }
        return formatter.date(from: string)
    }

    /// Human-readable rendering of a `yyyy-MM-dd` string, for display only.
    static func display(_ string: String?) -> String? {
        guard let date = date(from: string) else { return string }
        return date.formatted(.dateTime.month(.wide).day().year())
    }
}
