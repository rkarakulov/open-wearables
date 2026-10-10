"""Generic ANT+ FIT file parser — provider-agnostic (Garmin, Polar, Suunto, etc.)."""

import io
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import fitdecode

from app.schemas.enums.series_types import SeriesType
from app.schemas.model_crud.activities.data_point_series import TimeSeriesSampleCreate
from app.schemas.model_crud.activities.zones import HRZone, HRZones, PowerZone, PowerZones

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field map — ANT+ FIT Protocol standard field names → SeriesType
#
# fit_fields is ordered: first field present in the record message wins.
# This lets us prefer enhanced_* variants (FIT 2.0 float) over their uint16
# base equivalents without duplicating logic in the parser.
# ---------------------------------------------------------------------------

_SEMICIRCLES_TO_DEGREES: float = 180.0 / (2**31)


def _scale(factor: float) -> Callable[[float], Decimal]:
    def _fn(v: float) -> Decimal:
        return Decimal(str(round(v * factor, 6)))

    return _fn


@dataclass(frozen=True)
class _FieldMapping:
    fit_fields: tuple[str, ...]
    series_type: SeriesType
    transform: Callable[[float], Decimal] | None = None


_RECORD_FIELD_MAP: tuple[_FieldMapping, ...] = (
    _FieldMapping(("heart_rate",), SeriesType.heart_rate),
    _FieldMapping(("enhanced_speed", "speed"), SeriesType.speed),
    _FieldMapping(("cadence",), SeriesType.cadence),
    _FieldMapping(("power",), SeriesType.power),
    # FIT stores vertical_oscillation in mm, SeriesType unit is cm
    _FieldMapping(("vertical_oscillation",), SeriesType.running_vertical_oscillation, _scale(0.1)),
    _FieldMapping(("stance_time",), SeriesType.running_ground_contact_time),
    # FIT stores step_length in mm, SeriesType unit is cm
    _FieldMapping(("step_length",), SeriesType.running_stride_length, _scale(0.1)),
    _FieldMapping(("enhanced_altitude", "altitude"), SeriesType.elevation),
    _FieldMapping(("position_lat",), SeriesType.latitude, _scale(_SEMICIRCLES_TO_DEGREES)),
    _FieldMapping(("position_long",), SeriesType.longitude, _scale(_SEMICIRCLES_TO_DEGREES)),
    _FieldMapping(("temperature",), SeriesType.air_temperature),
    # fitdecode auto-applies scale=100 for these fields; value arrives already in percent
    _FieldMapping(("vertical_ratio",), SeriesType.running_vertical_ratio),
    _FieldMapping(("stance_time_balance",), SeriesType.running_stance_time_balance),
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionMetrics:
    """Whole-activity aggregates read from the FIT `session` message.

    Field names match the `workout_details` columns they are persisted into
    (``max_power`` → ``max_watts``, ``max_altitude``/``min_altitude`` → ``elev_high``/
    ``elev_low``) so callers can pass ``session_metrics.__dict__`` straight through to
    ``EventRecordDetailRepository.update_workout_fields``. Every field is optional —
    absent on files without a barometric altimeter / power meter, and the cycling
    dynamics fields (balance, pedal smoothness, torque effectiveness) additionally
    require an ANT+ power meter capable of reporting left/right data.
    """

    max_watts: Decimal | None = None
    max_speed: Decimal | None = None
    elev_high: Decimal | None = None
    elev_low: Decimal | None = None
    # Right-pedal power contribution %, decoded from the FIT `left_right_balance`
    # bitfield (see `_decode_left_right_balance`). None when the device didn't mark
    # the value as reliably attributable to a side.
    left_right_balance: Decimal | None = None
    avg_left_pedal_smoothness: Decimal | None = None
    avg_right_pedal_smoothness: Decimal | None = None
    avg_left_torque_effectiveness: Decimal | None = None
    avg_right_torque_effectiveness: Decimal | None = None


def session_metrics_fields(metrics: SessionMetrics | None) -> dict[str, Decimal]:
    """Non-None `SessionMetrics` fields, ready to merge into an
    `EventRecordDetailRepository.update_workout_fields` payload.

    Shared by every FIT-enriching provider (Garmin, Wahoo, ...) so each one doesn't
    reimplement "dataclass → sparse update dict" filtering.
    """
    if metrics is None:
        return {}
    return {k: v for k, v in metrics.__dict__.items() if v is not None}


@dataclass
class FitParseResult:
    samples: list[TimeSeriesSampleCreate] = field(default_factory=list)
    segments: list[dict] = field(default_factory=list)
    developer_fields_found: list[str] = field(default_factory=list)
    hr_zones: HRZones | None = None
    power_zones: PowerZones | None = None
    session_metrics: SessionMetrics | None = None


def parse_fit_file(
    data: bytes,
    user_id: UUID,
    data_source_id: UUID | None = None,
    source: str | None = None,
) -> FitParseResult:
    result = FitParseResult()
    dev_fields_seen: set[str] = set()
    _seg_counters: dict[str, int] = {k: 0 for k in _SEGMENT_KINDS}

    with fitdecode.FitReader(io.BytesIO(data)) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue

            if frame.name == "record":
                ts = _timestamp(frame)
                if ts is None:
                    continue
                for f in frame.fields:
                    if f.field is None and f.name not in (None, "unknown"):
                        dev_fields_seen.add(str(f.name))
                for mapping in _RECORD_FIELD_MAP:
                    if (value := _extract(frame, mapping)) is not None:
                        result.samples.append(
                            TimeSeriesSampleCreate(
                                id=uuid4(),
                                user_id=user_id,
                                data_source_id=data_source_id,
                                source=source,
                                recorded_at=ts,
                                zone_offset=None,
                                value=value,
                                series_type=mapping.series_type,
                            )
                        )

            elif frame.name == "time_in_zone":
                try:
                    ref = frame.get_value("reference_mesg")
                except KeyError:
                    ref = None
                if ref == "session":
                    result.hr_zones = _parse_hr_zones(frame)
                    result.power_zones = _parse_power_zones(frame)

            elif frame.name in _SEGMENT_KINDS:
                numeric, enums = _SEGMENT_KINDS[frame.name]
                idx = _seg_counters[frame.name]
                seg = _extract_segment(frame, frame.name, idx, numeric, enums)
                if seg.get("elapsed_seconds") is not None:
                    result.segments.append(seg)
                _seg_counters[frame.name] = idx + 1

            elif frame.name == "session":
                # Multi-sport files may carry more than one session message; last one wins.
                result.session_metrics = _parse_session_metrics(frame)

    result.developer_fields_found = sorted(dev_fields_seen)
    logger.debug(
        "FIT parsed: %d samples, %d segments, dev_fields=%s",
        len(result.samples),
        len(result.segments),
        result.developer_fields_found or "none",
    )
    return result


# ---------------------------------------------------------------------------
# Segment extraction helpers
# ---------------------------------------------------------------------------

# Field specs for each segment kind:
#   numeric: (fit_field_name, output_key, scale) — scale applied before rounding
#   enums:   (fit_field_name, output_key)        — stored as str()

_LAP_NUMERIC: tuple[tuple[str, str, float], ...] = (
    ("total_distance", "distance_meters", 1.0),
    ("avg_heart_rate", "avg_heart_rate", 1.0),
    ("max_heart_rate", "max_heart_rate", 1.0),
    ("avg_speed", "avg_speed", 1.0),
    ("max_speed", "max_speed", 1.0),
    ("avg_power", "avg_power", 1.0),
    ("max_power", "max_power", 1.0),
    ("normalized_power", "normalized_power", 1.0),
    ("avg_cadence", "avg_cadence", 1.0),
    ("total_strides", "total_strides", 1.0),
    ("total_ascent", "total_ascent", 1.0),
    ("total_descent", "total_descent", 1.0),
    # FIT stores in mm; output in cm
    ("avg_vertical_oscillation", "avg_vertical_oscillation", 0.1),
    ("avg_step_length", "avg_step_length", 0.1),
    # fitdecode auto-applies scale=100; value already in %
    ("avg_vertical_ratio", "avg_vertical_ratio", 1.0),
    ("avg_stance_time", "avg_stance_time", 1.0),
    ("avg_stance_time_balance", "avg_stance_time_balance", 1.0),
)

_SPLIT_NUMERIC: tuple[tuple[str, str, float], ...] = (
    ("total_distance", "distance_meters", 1.0),
    ("avg_speed", "avg_speed", 1.0),
)
_SPLIT_ENUMS: tuple[tuple[str, str], ...] = (("split_type", "split_type"),)

_LENGTH_NUMERIC: tuple[tuple[str, str, float], ...] = (
    ("total_strokes", "total_strokes", 1.0),
    ("avg_speed", "avg_speed", 1.0),
)
_LENGTH_ENUMS: tuple[tuple[str, str], ...] = (
    ("swim_stroke", "swim_stroke"),
    ("length_type", "length_type"),
)

# Dispatch map: frame.name → (numeric_fields, enum_fields)
_SEGMENT_KINDS: dict[str, tuple] = {
    "lap": (_LAP_NUMERIC, ()),
    "split": (_SPLIT_NUMERIC, _SPLIT_ENUMS),
    "length": (_LENGTH_NUMERIC, _LENGTH_ENUMS),
}


def _field_val(frame: fitdecode.FitDataMessage, name: str) -> Any:
    try:
        return frame.get_value(name)
    except KeyError:
        return None


def _r2(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _iso(v: Any) -> str | None:
    if not isinstance(v, datetime):
        return None
    ts = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return ts.isoformat()


def _extract_segment(
    frame: fitdecode.FitDataMessage,
    kind: str,
    index: int,
    numeric: tuple[tuple[str, str, float], ...],
    enums: tuple[tuple[str, str], ...] = (),
) -> dict:
    seg: dict = {
        "kind": kind,
        "index": index,
        "start_time": _iso(_field_val(frame, "start_time")),
        "elapsed_seconds": _r2(_field_val(frame, "total_elapsed_time")),
    }
    for fit_name, out_name, scale in numeric:
        v = _field_val(frame, fit_name)
        if v is not None:
            seg[out_name] = _r2(v * scale)
    for fit_name, out_name in enums:
        v = _field_val(frame, fit_name)
        if v is not None:
            seg[out_name] = str(v)
    return {k: v for k, v in seg.items() if v is not None}


# ---------------------------------------------------------------------------
# Record message helpers
# ---------------------------------------------------------------------------


def _timestamp(frame: fitdecode.FitDataMessage) -> datetime | None:
    try:
        ts = frame.get_value("timestamp")
    except KeyError:
        return None
    if not isinstance(ts, datetime):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _build_zone_entries(seconds_tuple: tuple, boundaries: tuple | None) -> list[tuple[float, int | None]]:
    """Pair time-in-zone seconds with upper boundaries.

    Boundaries define how many zones exist: N boundaries → N+1 zones (zone 0 through N),
    where zone N has no upper boundary. Trailing (0 s, no boundary) entries are stripped.
    """
    bl: list[int] = [b for b in (boundaries or []) if b is not None]
    entries = []
    for i in range(len(bl) + 1):
        secs = round(float(seconds_tuple[i]), 1) if i < len(seconds_tuple) else 0.0
        entries.append((secs, bl[i] if i < len(bl) else None))
    while entries and entries[-1] == (0.0, None):
        entries.pop()
    return entries


def _parse_hr_zones(frame: fitdecode.FitDataMessage) -> HRZones | None:
    entries = _build_zone_entries(
        _field_val(frame, "time_in_hr_zone") or (),
        _field_val(frame, "hr_zone_high_boundary"),
    )
    if not entries:
        return None
    raw_max_hr = _field_val(frame, "max_heart_rate")
    raw_threshold = _field_val(frame, "threshold_heart_rate")
    return HRZones(
        zones=[HRZone(zone=i, seconds=s, max_bpm=b) for i, (s, b) in enumerate(entries)],
        max_hr=int(raw_max_hr) if raw_max_hr else None,
        threshold_hr=int(raw_threshold) if raw_threshold else None,
    )


def _parse_power_zones(frame: fitdecode.FitDataMessage) -> PowerZones | None:
    entries = _build_zone_entries(
        _field_val(frame, "time_in_power_zone") or (),
        _field_val(frame, "power_zone_high_boundary"),
    )
    if not entries:
        return None
    raw_ftp = _field_val(frame, "functional_threshold_power")
    return PowerZones(
        zones=[PowerZone(zone=i, seconds=s, max_watts=b) for i, (s, b) in enumerate(entries)],
        ftp_watts=int(raw_ftp) if raw_ftp else None,
    )


# ---------------------------------------------------------------------------
# Session message helpers
# ---------------------------------------------------------------------------

# (fit_field names in priority order, output SessionMetrics field name).
# Prefers enhanced_* (FIT 2.0 float component) over the uint16 base field, like
# _RECORD_FIELD_MAP does for per-sample speed/altitude.
_SESSION_SIMPLE_FIELDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("max_power",), "max_watts"),
    (("enhanced_max_speed", "max_speed"), "max_speed"),
    (("enhanced_max_altitude", "max_altitude"), "elev_high"),
    (("enhanced_min_altitude", "min_altitude"), "elev_low"),
    (("avg_left_pedal_smoothness",), "avg_left_pedal_smoothness"),
    (("avg_right_pedal_smoothness",), "avg_right_pedal_smoothness"),
    (("avg_left_torque_effectiveness",), "avg_left_torque_effectiveness"),
    (("avg_right_torque_effectiveness",), "avg_right_torque_effectiveness"),
)


def _session_decimal(v: Any) -> Decimal | None:
    r = _r2(v)
    return Decimal(str(r)) if r is not None else None


def _decode_left_right_balance(raw: Any) -> Decimal | None:
    """Decode the FIT session `left_right_balance` field (16-bit bitfield, FIT type
    ``left_right_balance_100``).

    fitdecode does not auto-scale this field (no `scale=` on its Field definition —
    the FIT SDK models it as an enum/bitfield, not a plain scaled numeric), so the
    raw uint16 is decoded manually:
      - bits 0-13 (mask 0x3FFF): right-leg power contribution %, scaled by 100
        (e.g. raw 5000 → 50.00%).
      - bit 15 (0x8000): "right" flag. Per FIT SDK Profile.xlsx, the percentage only
        reliably corresponds to the right leg when this bit is set; when unset the
        side is not dependable, so the value is treated as unavailable rather than
        guessed.
    """
    if raw is None:
        return None
    try:
        raw_int = int(raw)
    except (TypeError, ValueError):
        return None
    if not (raw_int & 0x8000):
        return None
    return Decimal(raw_int & 0x3FFF) / Decimal(100)


def _parse_session_metrics(frame: fitdecode.FitDataMessage) -> SessionMetrics:
    values: dict[str, Decimal] = {}
    for fit_fields, out_key in _SESSION_SIMPLE_FIELDS:
        for fit_name in fit_fields:
            v = _field_val(frame, fit_name)
            if v is not None:
                decoded = _session_decimal(v)
                if decoded is not None:
                    values[out_key] = decoded
                break

    balance = _decode_left_right_balance(_field_val(frame, "left_right_balance"))
    if balance is not None:
        values["left_right_balance"] = balance

    return SessionMetrics(**values)


def _extract(frame: fitdecode.FitDataMessage, mapping: _FieldMapping) -> Decimal | None:
    for field_name in mapping.fit_fields:
        try:
            raw = frame.get_value(field_name)
            if raw is None:
                continue
            v = float(raw)
        except (KeyError, TypeError, ValueError):
            continue
        return mapping.transform(v) if mapping.transform else Decimal(str(v))
    return None
