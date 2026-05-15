import Combine
import Foundation
import HealthKit

@MainActor
final class HealthKitManager: ObservableObject {
    @Published private(set) var authorized: Bool = false
    @Published private(set) var lastSync: Date?
    @Published private(set) var lastError: String?

    private let store = HKHealthStore()
    private let anchorsKey = "coach.hk.anchors.v1"

    var isAvailable: Bool { HKHealthStore.isHealthDataAvailable() }

    func requestAuthorization() async {
        guard isAvailable else { return }
        do {
            try await store.requestAuthorization(toShare: [], read: HealthKitTypes.readTypes())
            authorized = true
            try await enableBackgroundDelivery()
            startObservers()
            await syncOnce()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func syncOnce() async {
        do {
            let batch = try await fetchDeltas()
            if batch.isEmpty { return }
            let _: EmptyResp = try await APIClient.shared.postJSON("/v1/ingest/healthkit", body: batch)
            lastSync = Date()
        } catch {
            lastError = error.localizedDescription
        }
    }

    // MARK: - background delivery

    private func enableBackgroundDelivery() async throws {
        for q in HealthKitTypes.quantityIdentifiers {
            guard let t = HKQuantityType.quantityType(forIdentifier: q) else { continue }
            try await withCheckedThrowingContinuation { cont in
                store.enableBackgroundDelivery(for: t, frequency: .immediate) { ok, err in
                    if let err { cont.resume(throwing: err) } else { cont.resume() }
                }
            }
        }
        if let sleep = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis) {
            try await withCheckedThrowingContinuation { cont in
                store.enableBackgroundDelivery(for: sleep, frequency: .hourly) { ok, err in
                    if let err { cont.resume(throwing: err) } else { cont.resume() }
                }
            }
        }
        try await withCheckedThrowingContinuation { cont in
            store.enableBackgroundDelivery(for: HKWorkoutType.workoutType(), frequency: .immediate) { _, err in
                if let err { cont.resume(throwing: err) } else { cont.resume() }
            }
        }
    }

    private func startObservers() {
        let types: [HKSampleType] = HealthKitTypes.quantityIdentifiers
            .compactMap { HKQuantityType.quantityType(forIdentifier: $0) }
            + [HKWorkoutType.workoutType()]
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
            batch.workouts.append(HKWorkoutDTO(
                source_record_id: w.uuid.uuidString,
                sport: w.workoutActivityType.coachName,
                started_at: w.startDate,
                ended_at: w.endDate,
                duration_s: Int(w.duration),
                distance_m: w.totalDistance?.doubleValue(for: .meter()),
                avg_hr: nil,
                max_hr: nil
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

        saveAnchors(anchors)
        return batch
    }

    private func fetchAnchored(type: HKSampleType, anchor: HKQueryAnchor?) async throws -> ([HKSample], HKQueryAnchor?) {
        try await withCheckedThrowingContinuation { cont in
            let q = HKAnchoredObjectQuery(
                type: type,
                predicate: nil,
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
