import Combine
import Foundation
import HealthKit

@MainActor
final class HealthKitManager: ObservableObject {
    /// Whether we have shown the system permission sheet.
    ///
    /// Deliberately NOT named `authorized`: HealthKit never reports read
    /// authorization. `requestAuthorization` succeeds whether the user grants or
    /// denies — telling us apart would leak what they chose, which Apple treats
    /// as private. So this only says "we asked", and it is persisted because the
    /// sheet is one-shot: asking twice does nothing, and the only way back after
    /// a denial is Settings.
    @Published private(set) var didRequestAuthorization: Bool =
        UserDefaults.standard.bool(forKey: HealthKitManager.askedKey)
    /// Whether Health has ever actually given us data. The only honest signal
    /// that the grant went through.
    @Published private(set) var hasData: Bool =
        UserDefaults.standard.bool(forKey: HealthKitManager.hasDataKey)
    @Published private(set) var lastSync: Date?
    @Published private(set) var lastError: String?

    private static let askedKey = "coach.hk.didRequestAuthorization"
    private static let hasDataKey = "coach.hk.hasData"

    /// How far back the first sync reaches.
    ///
    /// Without this the initial anchored query has no anchor and no predicate,
    /// so HealthKit returns the user's entire history — every HRV reading and
    /// sleep stage since they bought the watch. That was serialised into one
    /// JSON body and posted as a single request, which hung building it and then
    /// timed out at the proxy. The coach reasons over recent load anyway; a year
    /// of 2023 heart-rate samples adds nothing.
    private static let backfillDays = 90

    /// Maximum records per upload request. The batch is split so one enormous
    /// first sync becomes several ordinary requests instead of a single one that
    /// dies at the edge.
    private static let uploadChunkSize = 400

    private let store = HKHealthStore()
    private let anchorsKey = "coach.hk.anchors.v1"

    var isAvailable: Bool { HKHealthStore.isHealthDataAvailable() }

    func requestAuthorization() async {
        guard isAvailable else { return }
        do {
            try await store.requestAuthorization(toShare: [], read: HealthKitTypes.readTypes())
            didRequestAuthorization = true
            UserDefaults.standard.set(true, forKey: Self.askedKey)
        } catch {
            lastError = error.localizedDescription
            return
        }
        // Deliberately outside the do/catch above, and each step independent:
        // previously a single failing `enableBackgroundDelivery` call — which
        // happens when the entitlement isn't provisioned yet — threw past
        // `startObservers()` and `syncOnce()`, so no observers were registered
        // and the first sync never ran.
        do {
            try await enableBackgroundDelivery()
        } catch {
            lastError = error.localizedDescription
        }
        startObservers()
        await syncOnce()
    }

    func syncOnce() async {
        do {
            let batch = try await fetchDeltas()
            if batch.isEmpty {
                // An empty pull after authorization is ambiguous by design:
                // HealthKit returns no samples for types the user denied and
                // never reveals which. Saying so beats returning silently, since
                // the two cases need completely different actions from the user.
                if didRequestAuthorization, !hasData {
                    lastError = "Apple Health returned no data. Check that Coach Will "
                        + "is enabled in Settings › Privacy & Security › Health."
                }
                return
            }
            // Sent in chunks: the first sync after authorization can carry a
            // quarter's worth of samples, and as one request that body is large
            // enough to time out at the edge before the server sees it.
            for chunk in batch.chunked(max: Self.uploadChunkSize) {
                let _: EmptyResp = try await APIClient.shared.postJSON(
                    "/v1/ingest/healthkit", body: chunk
                )
            }
            lastSync = Date()
            lastError = nil
            if !hasData {
                hasData = true
                UserDefaults.standard.set(true, forKey: Self.hasDataKey)
            }
        } catch {
            lastError = error.localizedDescription
        }
    }

    /// Re-register observers on launch when the user has already been asked.
    /// Previously this happened only as a side effect of re-prompting every
    /// launch, because the asked-state wasn't persisted.
    func resumeIfAuthorized() async {
        guard isAvailable, didRequestAuthorization else { return }
        do {
            try await enableBackgroundDelivery()
        } catch {
            lastError = error.localizedDescription
        }
        startObservers()
        await syncOnce()
    }

    // MARK: - background delivery

    private func enableBackgroundDelivery() async throws {
        for q in HealthKitTypes.quantityIdentifiers {
            guard let t = HKQuantityType.quantityType(forIdentifier: q) else { continue }
            try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
                store.enableBackgroundDelivery(for: t, frequency: .immediate) { ok, err in
                    if let err { cont.resume(throwing: err) } else { cont.resume() }
                }
            }
        }
        if let sleep = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis) {
            try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
                store.enableBackgroundDelivery(for: sleep, frequency: .hourly) { ok, err in
                    if let err { cont.resume(throwing: err) } else { cont.resume() }
                }
            }
        }
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            store.enableBackgroundDelivery(for: HKWorkoutType.workoutType(), frequency: .immediate) { _, err in
                if let err { cont.resume(throwing: err) } else { cont.resume() }
            }
        }
    }

    private func startObservers() {
        // Sleep is included here on purpose: background delivery was being
        // enabled for `.sleepAnalysis` while nothing observed it, so a night's
        // sleep never triggered a sync of its own — it only arrived if some
        // other type happened to fire afterwards.
        let sleepType = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis)
        let types: [HKSampleType] = HealthKitTypes.quantityIdentifiers
            .compactMap { HKQuantityType.quantityType(forIdentifier: $0) }
            + [HKWorkoutType.workoutType()]
            + (sleepType.map { [$0] } ?? [])
        for t in types {
            let q = HKObserverQuery(sampleType: t, predicate: nil) { [weak self] _, completion, _ in
                Task { @MainActor in
                    await self?.syncOnce()
                    // ALWAYS call completion or iOS disables delivery after 3 misses.
                    completion()
                }
            }
            store.execute(q)
        }
    }

    // MARK: - delta fetching

    private func fetchDeltas() async throws -> HealthKitBatch {
        var batch = HealthKitBatch()
        var anchors = loadAnchors()

        // Workouts
        let (workouts, newWorkoutAnchor) = try await fetchAnchored(
            type: HKWorkoutType.workoutType(),
            anchor: anchors["workouts"]
        )
        for sample in workouts {
            guard let w = sample as? HKWorkout else { continue }
            // Heart rate is not carried on the workout itself; it lives in
            // separate samples that have to be queried over the workout's window.
            // These were previously sent as nil, which left every Apple Health
            // session with no intensity signal — and training load can't be
            // computed from duration alone.
            let hr = await heartRate(during: w)
            batch.workouts.append(HKWorkoutDTO(
                source_record_id: w.uuid.uuidString,
                sport: w.workoutActivityType.coachName,
                started_at: w.startDate,
                ended_at: w.endDate,
                duration_s: Int(w.duration),
                distance_m: w.totalDistance?.doubleValue(for: .meter()),
                avg_hr: hr.avg,
                max_hr: hr.max
            ))
        }
        anchors["workouts"] = newWorkoutAnchor

        // Sleep
        if let sleepType = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis) {
            let (sleeps, newAnchor) = try await fetchAnchored(type: sleepType, anchor: anchors["sleep"])
            var grouped: [String: HKSleepDTO] = [:]
            for s in sleeps.compactMap({ $0 as? HKCategorySample }) {
                let key = "\(s.startDate.timeIntervalSince1970)-\(s.endDate.timeIntervalSince1970)"
                var dto = grouped[key] ?? HKSleepDTO(
                    source_record_id: s.uuid.uuidString,
                    started_at: s.startDate,
                    ended_at: s.endDate,
                    deep_s: nil, light_s: nil, rem_s: nil, awake_s: nil
                )
                let secs = Int(s.endDate.timeIntervalSince(s.startDate))
                switch HKCategoryValueSleepAnalysis(rawValue: s.value) {
                case .asleepDeep:   dto.deep_s  = (dto.deep_s  ?? 0) + secs
                case .asleepCore:   dto.light_s = (dto.light_s ?? 0) + secs
                case .asleepREM:    dto.rem_s   = (dto.rem_s   ?? 0) + secs
                case .awake:        dto.awake_s = (dto.awake_s ?? 0) + secs
                default: break
                }
                grouped[key] = dto
            }
            batch.sleep.append(contentsOf: grouped.values)
            anchors["sleep"] = newAnchor
        }

        // HRV (SDNN)
        if let hrvType = HKQuantityType.quantityType(forIdentifier: .heartRateVariabilitySDNN) {
            let (samples, newAnchor) = try await fetchAnchored(type: hrvType, anchor: anchors["hrv"])
            for s in samples.compactMap({ $0 as? HKQuantitySample }) {
                let ms = s.quantity.doubleValue(for: .secondUnit(with: .milli))
                batch.hrv.append(HKHRVDTO(measured_at: s.startDate, sdnn_ms: ms))
            }
            anchors["hrv"] = newAnchor
        }

        // Resting HR → body_metrics for trend tracking
        if let restType = HKQuantityType.quantityType(forIdentifier: .restingHeartRate) {
            let (samples, newAnchor) = try await fetchAnchored(type: restType, anchor: anchors["rest_hr"])
            for s in samples.compactMap({ $0 as? HKQuantitySample }) {
                batch.body_metrics.append(HKBodyMetricDTO(
                    metric: "resting_hr",
                    value: s.quantity.doubleValue(for: HKUnit.count().unitDivided(by: .minute())),
                    measured_at: s.startDate
                ))
            }
            anchors["rest_hr"] = newAnchor
        }

        // VO2 max
        if let vo2 = HKQuantityType.quantityType(forIdentifier: .vo2Max) {
            let (samples, newAnchor) = try await fetchAnchored(type: vo2, anchor: anchors["vo2"])
            for s in samples.compactMap({ $0 as? HKQuantitySample }) {
                let unit = HKUnit(from: "ml/(kg*min)")
                batch.body_metrics.append(HKBodyMetricDTO(
                    metric: "vo2_max",
                    value: s.quantity.doubleValue(for: unit),
                    measured_at: s.startDate
                ))
            }
            anchors["vo2"] = newAnchor
        }

        // Body mass
        if let mass = HKQuantityType.quantityType(forIdentifier: .bodyMass) {
            let (samples, newAnchor) = try await fetchAnchored(type: mass, anchor: anchors["mass"])
            for s in samples.compactMap({ $0 as? HKQuantitySample }) {
                batch.body_metrics.append(HKBodyMetricDTO(
                    metric: "weight_kg",
                    value: s.quantity.doubleValue(for: .gramUnit(with: .kilo)),
                    measured_at: s.startDate
                ))
            }
            anchors["mass"] = newAnchor
        }

        // Daily activity totals.
        //
        // Everything above needs an Apple Watch: HRV, resting HR, VO2 max and
        // sleep simply don't exist on an iPhone alone. A user without a watch
        // therefore synced nothing at all, while Apple Health visibly held their
        // step count — which reads as the app being broken.
        //
        // Aggregated per day rather than sent raw: steps arrive as thousands of
        // small samples, and the coach reasons about daily totals anyway.
        batch.body_metrics.append(contentsOf: await dailyTotals())

        saveAnchors(anchors)
        return batch
    }

    /// Per-day sums for the activity metrics an iPhone records on its own.
    ///
    /// Uses the same 90-day window as the initial backfill. Re-sending a day is
    /// harmless: ingest upserts on (metric, measured_at), so a day that already
    /// exists is updated rather than duplicated — which is what we want, since
    /// today's total keeps changing until midnight.
    private func dailyTotals() async -> [HKBodyMetricDTO] {
        let wanted: [(HKQuantityTypeIdentifier, String, HKUnit)] = [
            (.stepCount, "steps", .count()),
            (.activeEnergyBurned, "active_energy_kcal", .kilocalorie()),
            (.appleExerciseTime, "exercise_minutes", .minute()),
        ]
        var out: [HKBodyMetricDTO] = []
        for (identifier, name, unit) in wanted {
            guard let type = HKQuantityType.quantityType(forIdentifier: identifier) else { continue }
            out.append(contentsOf: await dailySums(type: type, metric: name, unit: unit))
        }
        return out
    }

    private func dailySums(
        type: HKQuantityType, metric: String, unit: HKUnit
    ) async -> [HKBodyMetricDTO] {
        let cal = Calendar.current
        guard let start = cal.date(
            byAdding: .day, value: -Self.backfillDays, to: cal.startOfDay(for: Date())
        ) else { return [] }

        return await withCheckedContinuation { cont in
            let q = HKStatisticsCollectionQuery(
                quantityType: type,
                quantitySamplePredicate: HKQuery.predicateForSamples(
                    withStart: start, end: nil, options: .strictStartDate
                ),
                options: .cumulativeSum,
                anchorDate: start,
                intervalComponents: DateComponents(day: 1)
            )
            q.initialResultsHandler = { _, results, _ in
                var rows: [HKBodyMetricDTO] = []
                results?.enumerateStatistics(from: start, to: Date()) { stat, _ in
                    guard let sum = stat.sumQuantity()?.doubleValue(for: unit), sum > 0 else {
                        return
                    }
                    rows.append(HKBodyMetricDTO(
                        metric: metric, value: sum, measured_at: stat.startDate
                    ))
                }
                cont.resume(returning: rows)
            }
            store.execute(q)
        }
    }

    /// Average and peak heart rate across a workout.
    ///
    /// One statistics query per workout rather than pulling every sample and
    /// bucketing them: HealthKit does the aggregation, so a 90-day backfill
    /// doesn't drag tens of thousands of individual readings through memory.
    ///
    /// Returns nils rather than throwing. A workout recorded without a heart
    /// rate monitor is normal, not an error, and must not fail the whole sync.
    private func heartRate(during workout: HKWorkout) async -> (avg: Int?, max: Int?) {
        guard let hrType = HKQuantityType.quantityType(forIdentifier: .heartRate) else {
            return (nil, nil)
        }
        let predicate = HKQuery.predicateForSamples(
            withStart: workout.startDate, end: workout.endDate, options: .strictStartDate
        )
        let bpm = HKUnit.count().unitDivided(by: .minute())

        return await withCheckedContinuation { cont in
            let q = HKStatisticsQuery(
                quantityType: hrType,
                quantitySamplePredicate: predicate,
                options: [.discreteAverage, .discreteMax]
            ) { _, stats, _ in
                let avg = stats?.averageQuantity()?.doubleValue(for: bpm)
                let peak = stats?.maximumQuantity()?.doubleValue(for: bpm)
                cont.resume(returning: (avg.map { Int($0.rounded()) },
                                        peak.map { Int($0.rounded()) }))
            }
            store.execute(q)
        }
    }

    private func fetchAnchored(type: HKSampleType, anchor: HKQueryAnchor?) async throws -> ([HKSample], HKQueryAnchor?) {
        // Only the *first* query needs bounding. Once an anchor exists HealthKit
        // returns deltas since that anchor, which is inherently small, and a
        // predicate would then wrongly exclude backdated samples that arrive
        // late (a watch syncing after being offline, for instance).
        let predicate: NSPredicate? = anchor == nil
            ? HKQuery.predicateForSamples(
                withStart: Calendar.current.date(
                    byAdding: .day, value: -Self.backfillDays, to: Date()
                ),
                end: nil,
                options: .strictStartDate
              )
            : nil

        return try await withCheckedThrowingContinuation { cont in
            let q = HKAnchoredObjectQuery(
                type: type,
                predicate: predicate,
                anchor: anchor,
                limit: HKObjectQueryNoLimit
            ) { _, samples, _, newAnchor, error in
                if let error { cont.resume(throwing: error); return }
                cont.resume(returning: (samples ?? [], newAnchor))
            }
            store.execute(q)
        }
    }

    private func loadAnchors() -> [String: HKQueryAnchor] {
        guard let data = UserDefaults.standard.data(forKey: anchorsKey),
              let raw = try? NSKeyedUnarchiver.unarchiveTopLevelObjectWithData(data) as? [String: Data] else {
            return [:]
        }
        var result: [String: HKQueryAnchor] = [:]
        for (k, v) in raw {
            if let a = try? NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: v) {
                result[k] = a
            }
        }
        return result
    }

    private func saveAnchors(_ anchors: [String: HKQueryAnchor]) {
        var raw: [String: Data] = [:]
        for (k, v) in anchors {
            if let d = try? NSKeyedArchiver.archivedData(withRootObject: v, requiringSecureCoding: true) {
                raw[k] = d
            }
        }
        if let data = try? NSKeyedArchiver.archivedData(withRootObject: raw, requiringSecureCoding: true) {
            UserDefaults.standard.set(data, forKey: anchorsKey)
        }
    }
}

private struct EmptyResp: Decodable {}

// MARK: - DTOs matching backend Pydantic models

struct HealthKitBatch: Encodable {
    var workouts: [HKWorkoutDTO] = []
    var sleep: [HKSleepDTO] = []
    var hrv: [HKHRVDTO] = []
    var heart_rate: [HKHRDTO] = []
    var body_metrics: [HKBodyMetricDTO] = []

    var isEmpty: Bool {
        workouts.isEmpty && sleep.isEmpty && hrv.isEmpty && heart_rate.isEmpty && body_metrics.isEmpty
    }

    /// Split into batches of at most `max` records each.
    ///
    /// Each chunk is a complete, independently valid payload, so a failure part
    /// way through leaves the earlier chunks already ingested rather than losing
    /// the whole sync. Ingest is idempotent on `source_record_id`, so a retry
    /// re-sending an accepted chunk is harmless.
    func chunked(max: Int) -> [HealthKitBatch] {
        var out: [HealthKitBatch] = []
        var current = HealthKitBatch()
        var count = 0

        func flushIfFull() {
            if count >= max {
                out.append(current)
                current = HealthKitBatch()
                count = 0
            }
        }

        for w in workouts { current.workouts.append(w); count += 1; flushIfFull() }
        for s in sleep { current.sleep.append(s); count += 1; flushIfFull() }
        for h in hrv { current.hrv.append(h); count += 1; flushIfFull() }
        for h in heart_rate { current.heart_rate.append(h); count += 1; flushIfFull() }
        for b in body_metrics { current.body_metrics.append(b); count += 1; flushIfFull() }

        if !current.isEmpty { out.append(current) }
        return out
    }
}

struct HKWorkoutDTO: Encodable {
    let source_record_id: String
    let sport: String?
    let started_at: Date
    let ended_at: Date
    let duration_s: Int
    let distance_m: Double?
    let avg_hr: Int?
    let max_hr: Int?
}

struct HKSleepDTO: Encodable {
    let source_record_id: String
    let started_at: Date
    let ended_at: Date
    var deep_s: Int?
    var light_s: Int?
    var rem_s: Int?
    var awake_s: Int?
}

struct HKHRVDTO: Encodable {
    let measured_at: Date
    let sdnn_ms: Double
}

struct HKHRDTO: Encodable {
    let measured_at: Date
    let bpm: Int
    let context: String?
}

struct HKBodyMetricDTO: Encodable {
    let metric: String  // weight_kg | body_fat_pct | vo2_max | resting_hr
    let value: Double
    let measured_at: Date
}

private extension HKWorkoutActivityType {
    var coachName: String {
        switch self {
        case .running: return "running"
        case .cycling: return "cycling"
        case .swimming: return "swimming"
        case .traditionalStrengthTraining, .functionalStrengthTraining: return "strength"
        case .hiking: return "hiking"
        case .yoga: return "yoga"
        case .walking: return "walking"
        default: return "other"
        }
    }
}
