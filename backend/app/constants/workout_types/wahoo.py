from app.schemas.enums import WorkoutType

# Wahoo Cloud API `workout_type_id` values mapped to unified WorkoutType.
# Source: https://cloud-api.wahooligan.com/ (Workout object `workout_type_id` reference).
WORKOUT_TYPE_ID_TO_UNIFIED: dict[int, WorkoutType] = {
    0: WorkoutType.CYCLING,  # BIKING
    1: WorkoutType.RUNNING,  # RUNNING
    2: WorkoutType.FITNESS_EQUIPMENT,  # FE
    3: WorkoutType.RUNNING,  # RUNNING_TRACK
    4: WorkoutType.TRAIL_RUNNING,  # RUNNING_TRAIL
    5: WorkoutType.TREADMILL,  # RUNNING_TREADMILL
    6: WorkoutType.WALKING,  # WALKING
    7: WorkoutType.WALKING,  # WALKING_SPEED
    8: WorkoutType.WALKING,  # WALKING_NORDIC
    9: WorkoutType.HIKING,  # HIKING
    10: WorkoutType.MOUNTAINEERING,  # MOUNTAINEERING
    11: WorkoutType.CYCLOCROSS,  # BIKING_CYCLECROSS
    12: WorkoutType.INDOOR_CYCLING,  # BIKING_INDOOR
    13: WorkoutType.MOUNTAIN_BIKING,  # BIKING_MOUNTAIN
    14: WorkoutType.CYCLING,  # BIKING_RECUMBENT
    15: WorkoutType.CYCLING,  # BIKING_ROAD
    16: WorkoutType.CYCLING,  # BIKING_TRACK
    17: WorkoutType.MOTORCYCLING,  # BIKING_MOTOCYCLING
    18: WorkoutType.FITNESS_EQUIPMENT,  # FE_GENERAL
    19: WorkoutType.TREADMILL,  # FE_TREADMILL
    20: WorkoutType.ELLIPTICAL,  # FE_ELLIPTICAL
    21: WorkoutType.INDOOR_CYCLING,  # FE_BIKE
    22: WorkoutType.ROWING_MACHINE,  # FE_ROWER
    23: WorkoutType.STAIR_CLIMBING,  # FE_CLIMBER
    25: WorkoutType.POOL_SWIMMING,  # SWIMMING_LAP
    26: WorkoutType.OPEN_WATER_SWIMMING,  # SWIMMING_OPEN_WATER
    27: WorkoutType.SNOWBOARDING,  # SNOWBOARDING
    28: WorkoutType.ALPINE_SKIING,  # SKIING (generic — no unified "skiing" category)
    29: WorkoutType.ALPINE_SKIING,  # SKIING_DOWNHILL
    30: WorkoutType.CROSS_COUNTRY_SKIING,  # SKIINGCROSS_COUNTRY
    31: WorkoutType.SKATING,  # SKATING
    32: WorkoutType.ICE_SKATING,  # SKATING_ICE
    33: WorkoutType.INLINE_SKATING,  # SKATING_INLINE
    34: WorkoutType.SKATEBOARDING,  # LONG_BOARDING (closest unified match)
    35: WorkoutType.SAILING,  # SAILING
    36: WorkoutType.WINDSURFING,  # WINDSURFING
    37: WorkoutType.CANOEING,  # CANOEING
    38: WorkoutType.KAYAKING,  # KAYAKING
    39: WorkoutType.ROWING,  # ROWING
    40: WorkoutType.KITESURFING,  # KITEBOARDING
    41: WorkoutType.STAND_UP_PADDLEBOARDING,  # STAND_UP_PADDLE_BOARD
    42: WorkoutType.GENERIC,  # WORKOUT
    43: WorkoutType.CARDIO_TRAINING,  # CARDIO_CLASS
    44: WorkoutType.STAIR_CLIMBING,  # STAIR_CLIMBER
    45: WorkoutType.WHEELCHAIR,  # WHEELCHAIR
    46: WorkoutType.GOLF,  # GOLFING
    47: WorkoutType.OTHER,  # OTHER
    49: WorkoutType.INDOOR_CYCLING,  # BIKING_INDOOR_CYCLING_CLASS
    56: WorkoutType.TREADMILL,  # WALKING_TREADMILL
    61: WorkoutType.INDOOR_CYCLING,  # BIKING_INDOOR_TRAINER
    62: WorkoutType.MULTISPORT,  # MULTISPORT
    63: WorkoutType.TRANSITION,  # TRANSITION
    64: WorkoutType.E_BIKING,  # EBIKING
    65: WorkoutType.OTHER,  # TICKR_OFFLINE (heart-rate-monitor-only recording, not a sport)
    66: WorkoutType.YOGA,  # YOGA
    67: WorkoutType.RUNNING,  # RUNNING_RACE
    68: WorkoutType.INDOOR_CYCLING,  # BIKING_INDOOR_VIRTUAL
    69: WorkoutType.MEDITATION,  # MENTAL_STRENGTH (closest unified mind-body match)
    70: WorkoutType.CYCLING,  # HANDCYCLING
    71: WorkoutType.RUNNING,  # RUNNING_INDOOR_VIRTUAL
    255: WorkoutType.OTHER,  # UNKNOWN
}


def get_unified_workout_type(workout_type_id: int | str | None) -> WorkoutType:
    """Convert Wahoo's `workout_type_id` to the unified WorkoutType.

    Args:
        workout_type_id: Wahoo workout_type_id (e.g. 0 for BIKING, 1 for RUNNING).

    Returns:
        Unified WorkoutType enum value, defaulting to OTHER when unmapped or missing.
    """
    if workout_type_id is None:
        return WorkoutType.OTHER
    try:
        type_id = int(workout_type_id)
    except (TypeError, ValueError):
        return WorkoutType.OTHER
    return WORKOUT_TYPE_ID_TO_UNIFIED.get(type_id, WorkoutType.OTHER)
