from app.schemas.enums import EntrySource


def get_unified_entry_source(manual: bool | None) -> EntrySource | None:
    """Convert Wahoo's `workout_summary.manual` flag to the unified EntrySource."""
    if manual is None:
        return None
    return EntrySource.MANUAL if manual else EntrySource.AUTOMATIC
