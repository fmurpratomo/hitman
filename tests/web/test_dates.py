from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from hitman.web.dates import day_label, group_by_day, local_day


@dataclass
class Entry:
    """Only the field the grouping reads."""

    created_at: str


def local(days_ago=0, hour=12):
    """A stamp for a moment in the machine's own timezone, as the store writes it."""
    moment = datetime.now().astimezone().replace(hour=hour) - timedelta(days=days_ago)
    return Entry(moment.isoformat(timespec="seconds"))


def labels(entries, today=None):
    return [label for label, _ in group_by_day(entries, today)]


def test_the_most_recent_day_comes_first():
    groups = group_by_day([local(0), local(0), local(1), local(5)])
    assert [label for label, _ in groups][:2] == ["Today", "Yesterday"]
    assert [len(items) for _, items in groups] == [2, 1, 1]


def test_today_and_yesterday_are_named_not_dated():
    assert labels([local(0)]) == ["Today"]
    assert labels([local(1)]) == ["Yesterday"]


def test_older_days_in_this_year_show_a_weekday_and_date():
    today = date(2026, 9, 3)
    assert day_label(date(2026, 8, 31), today) == "Mon 31 Aug"


def test_a_day_in_another_year_carries_the_year():
    assert day_label(date(2025, 12, 24), date(2026, 9, 3)) == "24 Dec 2025"


def test_the_day_number_is_not_zero_padded():
    assert day_label(date(2026, 9, 1), date(2026, 9, 3)) == "Tue 1 Sep"


def test_the_same_instant_written_in_two_offsets_lands_in_one_group():
    """Proof the bucket is the instant's local day, not the date as written.

    19:00 UTC and 04:00 the next morning in +09:00 are the same moment. Split
    on the written date they land on different days; converted, they cannot.
    """
    utc = datetime(2026, 9, 2, 19, 0, tzinfo=timezone.utc)
    elsewhere = utc.astimezone(timezone(timedelta(hours=9)))
    assert elsewhere.date() != utc.date()  # the two spellings really do differ

    groups = group_by_day([Entry(utc.isoformat()), Entry(elsewhere.isoformat())])
    assert len(groups) == 1
    assert len(groups[0][1]) == 2


def test_a_stamp_is_bucketed_by_the_local_date_not_the_utc_one():
    moment = datetime(2026, 9, 2, 19, 0, tzinfo=timezone.utc)
    assert local_day(moment.isoformat()) == moment.astimezone().date()


def test_a_stamp_without_an_offset_is_read_as_local():
    assert local_day("2026-09-02T19:00:00") == date(2026, 9, 2)


def test_an_unreadable_stamp_gets_its_own_group_rather_than_crashing():
    groups = group_by_day([Entry("not a date"), local(0)])
    assert "Undated" in [label for label, _ in groups]


def test_nothing_to_group_is_no_groups():
    assert group_by_day([]) == []


def test_days_come_out_newest_first_whatever_order_they_arrived_in():
    """Insertion order tracks time only while the clock moves forward."""
    groups = group_by_day([local(9), local(0), local(1)])
    assert [label for label, _ in groups] == ["Today", "Yesterday", labels([local(9)])[0]]


def test_the_undated_bucket_sorts_last():
    groups = group_by_day([Entry("not a date"), local(0)])
    assert [label for label, _ in groups] == ["Today", "Undated"]


def test_a_clock_skewed_row_joins_its_own_day_rather_than_repeating_it():
    """Bucketing by date, not by run, so a heading never appears twice."""
    groups = group_by_day([local(0), local(1), local(0)])
    assert [label for label, _ in groups] == ["Today", "Yesterday"]
    assert len(groups[0][1]) == 2
