"""Group history entries into the days they happened on.

Timestamps are stored in UTC, but a day is a local idea: a request sent at
02:00 in Jakarta happened on the previous UTC date, and filing it under
yesterday would be wrong for the only person who ever sees it. Every stamp is
therefore converted to the machine's own timezone before its date is taken,
which is the right answer here precisely because the server and the person
reading the list are on the same machine.

No display strings live in ``core``; this is the view's business.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta


def local_day(stamp: str) -> date | None:
    """The local calendar date a stored timestamp falls on."""
    try:
        moment = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    # A stamp written without an offset predates nothing in this app, but
    # treating it as local rather than crashing is the harmless reading.
    return moment.date() if moment.tzinfo is None else moment.astimezone().date()


def day_label(day: date | None, today: date) -> str:
    if day is None:
        return "Undated"
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    # Built from the parts rather than strftime("%-d"): the no-padding flag is
    # a platform extension and is not portable.
    if day.year == today.year:
        return f"{day:%a} {day.day} {day:%b}"
    return f"{day.day} {day:%b} {day.year}"


def group_by_day(entries: list, today: date | None = None) -> list:
    """``[(label, entries)]``, newest day first, undated last.

    Days are sorted here rather than inherited from the caller. History is
    listed by insertion order, which matches time only for as long as the clock
    moves forward — order the days explicitly and a row written after a
    backwards clock correction joins its own day instead of dragging that whole
    day to the top of the list.

    Within a day the entries keep the order they arrived in, so the newest send
    stays first. Bucketing by date rather than by run also means a heading can
    never appear twice.
    """
    if today is None:
        today = datetime.now().astimezone().date()

    buckets: dict = {}
    for entry in entries:
        buckets.setdefault(local_day(entry.created_at), []).append(entry)

    # (day is not None, day) reversed puts the latest date first and leaves the
    # undated bucket, if any, at the end.
    days = sorted(buckets, key=lambda day: (day is not None, day or date.min), reverse=True)
    return [(day_label(day, today), buckets[day]) for day in days]
