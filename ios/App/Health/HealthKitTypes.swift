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
        // Nutrition. MyFitnessPal, Cronometer and Lose It all write here, which
        // is how we read a food log without a partnership with any of them —
        // MyFitnessPal closed its public API in 2019 and does not accept new
        // developers. Absent for anyone not logging food, and that is fine: the
        // queries simply return nothing.
        .dietaryEnergyConsumed,
        .dietaryProtein,
        .dietaryCarbohydrates,
        .dietaryFatTotal,
        .dietaryWater,
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
