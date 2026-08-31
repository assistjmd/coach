import SwiftUI
import UIKit

/// The daily-reminder control.
///
/// A coach you have to remember to consult is not a coach. This is the one
/// mechanic that turns the app from something you check into something that
/// checks on you, so it sits above the destructive actions rather than buried
/// under them.
struct ReminderPanel: View {
    @ObservedObject private var nudge = MorningNudge.shared
    @State private var showingPicker = false

    /// Bridges the stored hour/minute to the DatePicker's `Date`. Only the time
    /// components are ever read back, so the date part is arbitrary.
    private var time: Binding<Date> {
        Binding(
            get: {
                Calendar.current.date(
                    bySettingHour: nudge.hour, minute: nudge.minute, second: 0, of: Date()
                ) ?? Date()
            },
            set: { new in
                let c = Calendar.current.dateComponents([.hour, .minute], from: new)
                Task { await nudge.chooseTime(hour: c.hour ?? 6, minute: c.minute ?? 30) }
            }
        )
    }

    var body: some View {
        Panel(accent: nudge.enabled ? Theme.Palette.accent : nil) {
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .center) {
                    VStack(alignment: .leading, spacing: 4) {
                        SectionLabel("Daily reminder")
                        Text(nudge.enabled ? nudge.timeLabel : "Off")
                            .font(Theme.Font.stat(24))
                            .foregroundStyle(
                                nudge.enabled ? Theme.Palette.accent : Theme.Palette.textDim
                            )
                    }
                    Spacer(minLength: 8)
                    control
                }

                if nudge.enabled {
                    Rectangle().fill(Theme.Palette.line).frame(height: 1)
                    DatePicker(
                        "", selection: time, displayedComponents: .hourAndMinute
                    )
                    .datePickerStyle(.compact)
                    .labelsHidden()
                    .tint(Theme.Palette.accent)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

                Text(caption)
                    .font(.system(size: 12))
                    .foregroundStyle(Theme.Palette.textDim)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .task { await nudge.refresh() }
    }

    @ViewBuilder
    private var control: some View {
        if nudge.denied {
            // iOS ignores a second authorization request once denied, so a
            // toggle here would do nothing at all. Send them where it can
            // actually be changed.
            Button("SETTINGS") {
                if let url = URL(string: UIApplication.openSettingsURLString) {
                    UIApplication.shared.open(url)
                }
            }
            .font(.system(size: 12, weight: .heavy))
            .tracking(1.1)
            .tint(Theme.Palette.caution)
        } else {
            Toggle(
                "",
                isOn: Binding(
                    get: { nudge.enabled },
                    set: { on in
                        if on { Task { await nudge.enable() } } else { nudge.disable() }
                    }
                )
            )
            .labelsHidden()
            .tint(Theme.Palette.accent)
        }
    }

    private var caption: String {
        if nudge.denied {
            return "Notifications are off for Coach Will. Turn them on in "
                + "Settings › Notifications to get the reminder."
        }
        // Says what it will and won't contain. On a health app the second half
        // is the part people actually want to know.
        return "A nudge that your brief is ready. It never shows your readiness "
            + "or your session on the lock screen — you open the app for that."
    }
}
