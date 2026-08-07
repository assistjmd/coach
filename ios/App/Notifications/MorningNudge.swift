import Foundation
import UserNotifications

/// The daily reminder that a brief is waiting.
///
/// Local, not push. The brief is generated server-side at 6am in the athlete's
/// own timezone, and a local notification scheduled for a wall-clock time needs
/// no APNs certificate, no device-token registry, and no server round trip —
/// iOS fires it whether or not the phone has signal.
///
/// The text is deliberately empty of content: "Your brief is ready", never the
/// readiness score or the session. A lock screen is visible to whoever is
/// standing nearby, and this app's whole position is that it does not leak your
/// health data. That includes leaking it to the person behind you in a queue.
///
/// The cost of being local is that it fires on schedule regardless of whether
/// generation actually succeeded. That is the right trade at this size: a rare
/// tap into an empty state beats a push stack nobody is maintaining.
@MainActor
final class MorningNudge: ObservableObject {
    static let shared = MorningNudge()

    /// Identifier is fixed so re-scheduling replaces rather than accumulates.
    /// Without this, changing the time twice would leave three notifications.
    private static let requestID = "morning-brief"

    private enum Key {
        static let enabled = "nudge.enabled"
        static let hour = "nudge.hour"
        static let minute = "nudge.minute"
    }

    @Published private(set) var enabled: Bool
    @Published private(set) var hour: Int
    @Published private(set) var minute: Int
    /// True once the system prompt has been answered with "Don't Allow". The UI
    /// needs this to point at Settings instead of re-asking, since iOS silently
    /// ignores a second request.
    @Published private(set) var denied = false

    private init() {
        let d = UserDefaults.standard
        enabled = d.bool(forKey: Key.enabled)
        // 6:30, half an hour after the brief job runs, so the content exists by
        // the time anyone taps through. `object(forKey:)` rather than
        // `integer(forKey:)` because an unset integer reads as 0 — which would
        // silently mean midnight.
        hour = d.object(forKey: Key.hour) as? Int ?? 6
        minute = d.object(forKey: Key.minute) as? Int ?? 30
    }

    var timeLabel: String {
        String(format: "%d:%02d %@", hour % 12 == 0 ? 12 : hour % 12, minute,
               hour < 12 ? "AM" : "PM")
    }

    /// Ask for permission and turn the reminder on.
    ///
    /// Returns false when the user declined, so the caller can render the
    /// Settings path rather than a toggle that silently does nothing.
    @discardableResult
    func enable() async -> Bool {
        let center = UNUserNotificationCenter.current()
        let granted = (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
        denied = !granted
        guard granted else {
            setEnabled(false)
            return false
        }
        setEnabled(true)
        await reschedule()
        return true
    }

    func disable() {
        setEnabled(false)
        UNUserNotificationCenter.current()
            .removePendingNotificationRequests(withIdentifiers: [Self.requestID])
    }

    func setTime(hour: Int, minute: Int) async {
        self.hour = hour
        self.minute = minute
        let d = UserDefaults.standard
        d.set(hour, forKey: Key.hour)
        d.set(minute, forKey: Key.minute)
        guard enabled else { return }
        await reschedule()
    }

    /// Re-arm on launch.
    ///
    /// Two things make this necessary rather than redundant. iOS drops pending
    /// requests when an app is reinstalled or restored to a new device, and a
    /// user who revokes notifications in Settings leaves `enabled` true here
    /// with nothing actually scheduled. Checking real authorization on every
    /// launch keeps our state and the system's from drifting apart.
    func refresh() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        denied = settings.authorizationStatus == .denied
        guard enabled, settings.authorizationStatus == .authorized else { return }
        await reschedule()
    }

    private func setEnabled(_ value: Bool) {
        enabled = value
        UserDefaults.standard.set(value, forKey: Key.enabled)
    }

    private func reschedule() async {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [Self.requestID])

        let content = UNMutableNotificationContent()
        content.title = "Coach Will"
        content.body = "Your brief is ready."
        content.sound = .default

        // No explicit timezone: DateComponents without one resolves against the
        // device's current zone, so the reminder follows the athlete across
        // timezones instead of firing at 6:30 Boston time in Tokyo.
        var when = DateComponents()
        when.hour = hour
        when.minute = minute

        try? await center.add(
            UNNotificationRequest(
                identifier: Self.requestID,
                content: content,
                trigger: UNCalendarNotificationTrigger(dateMatching: when, repeats: true)
            )
        )
    }
}
