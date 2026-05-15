import HealthKit

enum HealthKitTypes {
    static let quantityIdentifiers: [HKQuantityTypeIdentifier] = [
        .heartRate,
        .restingHeartRate,
        .heartRateVariabilitySDNN,
        .vo2Max,
        .activeEnergyBurned,
        .basalEnergyBurned,
        .stepCount,
        .distanceWalkingRunning,
        .appleExerciseTime,
        .bodyMass,
        .bodyFatPercentage,
        .respiratoryRate,
        .oxygenSaturation,
        .appleSleepingWristTemperature,
    ]

    static let categoryIdentifiers: [HKCategoryTypeIdentifier] = [.sleepAnalysis]

    static func readTypes() -> Set<HKObjectType> {
        var set = Set<HKObjectType>()
        for q in quantityIdentifiers {
            if let t = HKQuantityType.quantityType(forIdentifier: q) { set.insert(t) }
        }
        for c in categoryIdentifiers {
            if let t = HKCategoryType.categoryType(forIdentifier: c) { set.insert(t) }
        }
        set.insert(HKWorkoutType.workoutType())
        set.insert(HKSeriesType.workoutRoute())
        return set
    }
}
