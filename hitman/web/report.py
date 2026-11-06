"""The aggregate report, and the CSV a spreadsheet opens.

The table built here is JMeter's Aggregate Report, column for column: one row
per request label, then a TOTAL row over every sample. That shape is the point
— it is what a performance report is expected to look like, and what anyone
who has read one before already knows how to read.

Two things are computed rather than stored. Throughput is samples over the
run's whole duration, not over the span a single label happened to be active,
which is what JMeter reports and what makes the label rows add up to TOTAL.
Percentiles come from the run's bucketed latency and are within 1% of exact;
the TOTAL row's are taken over all samples, never averaged from the rows.

Nothing here belongs in ``core``: these are the numbers a report puts on a
page, and a run is summarised without them.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime

from hitman.core.loadtest import LoadResult

# Columns of the aggregate table, in JMeter's order. Held in one place because
# the CSV writes them as a header row and the print page as table cells, and
# the two drifting apart is the obvious way for this to go wrong.
AGGREGATE_COLUMNS = (
    "Label",
    "# Samples",
    "Average",
    "Median",
    "90% Line",
    "95% Line",
    "99% Line",
    "Min",
    "Max",
    "Error %",
    "Throughput",
    "Received KB/sec",
    "Avg. Bytes",
)


@dataclass
class AggregateRow:
    """One line of the aggregate table. Milliseconds, except where named."""

    label: str
    samples: int
    failed: int
    mean: float
    median: float
    p90: float
    p95: float
    p99: float
    min: float
    max: float
    error_pct: float
    throughput: float
    # None, not zero, when the run never recorded response sizes: a report
    # that prints 0 KB/sec for a server that was in fact serving is lying.
    kb_per_s: float | None
    avg_bytes: float | None
    is_total: bool = False


def aggregate(result: LoadResult) -> list[AggregateRow]:
    """The aggregate table: a row per label, then TOTAL.

    The TOTAL row is kept even for a single-label run, as JMeter keeps it. It
    costs one line and it is where the eye goes first.
    """
    seconds = result.elapsed_ms / 1000
    measured = result.measured_bytes
    rows = [
        _row(
            label=item.label or "(unnamed)",
            samples=item.requests,
            failed=item.failed,
            latency=item.latency,
            total_bytes=item.bytes_received,
            seconds=seconds,
            measured=measured,
        )
        for item in result.labels
    ]
    rows.append(
        _row(
            label="TOTAL",
            samples=result.requests,
            failed=result.failed,
            latency=result.latency,
            total_bytes=result.bytes_received,
            seconds=seconds,
            measured=measured,
            is_total=True,
        )
    )
    return rows


def _row(
    *, label, samples, failed, latency, total_bytes, seconds, measured, is_total=False
) -> AggregateRow:
    return AggregateRow(
        label=label,
        samples=samples,
        failed=failed,
        mean=latency.mean,
        median=latency.p50,
        p90=latency.p90,
        p95=latency.p95,
        p99=latency.p99,
        min=latency.min,
        max=latency.max,
        error_pct=100 * failed / samples if samples else 0.0,
        throughput=samples / seconds if seconds > 0 else 0.0,
        # measured is the run's answer, not this row's: a label that happens to
        # return empty bodies in a run that recorded sizes reports 0, truthfully.
        kb_per_s=(total_bytes / 1024 / seconds if seconds > 0 else 0.0) if measured else None,
        avg_bytes=(total_bytes / samples if samples else 0.0) if measured else None,
        is_total=is_total,
    )


def status_rows(result: LoadResult) -> list[tuple[str, int, float]]:
    """Response codes with their share of the run, highest count first."""
    total = result.requests or 1
    return [
        (code, count, 100 * count / total)
        for code, count in sorted(result.statuses, key=lambda pair: -pair[1])
    ]


# --- CSV ----------------------------------------------------------------


def report_csv(result: LoadResult, generated: datetime | None = None) -> str:
    """The whole report as one CSV, in labelled sections.

    Sections rather than a single table because this is a report, not a data
    extract: the aggregate table, the codes and the timeline answer different
    questions and have different columns. A spreadsheet opens the file and
    shows them stacked with a blank line between, which is how a person reads
    it anyway. The aggregate table comes first because it is the one people
    copy out.

    Numbers are written as numbers — no units, no thousands separators, no
    percent signs — so that sorting and charting in the spreadsheet work.
    Units live in the column headings.
    """
    buffer = io.StringIO()
    # The RFC line ending, which is also what Excel expects on every platform.
    writer = csv.writer(buffer, lineterminator="\r\n")

    writer.writerow(["Hitman performance report"])
    for key, value in summary_pairs(result, generated):
        writer.writerow([key, value])

    _blank(writer)
    writer.writerow(["Aggregate report"])
    writer.writerow(
        [
            "Label",
            "# Samples",
            "Average (ms)",
            "Median (ms)",
            "90% Line (ms)",
            "95% Line (ms)",
            "99% Line (ms)",
            "Min (ms)",
            "Max (ms)",
            "Error %",
            "Throughput (req/s)",
            "Received KB/sec",
            "Avg. Bytes",
        ]
    )
    for row in aggregate(result):
        writer.writerow(
            [
                row.label,
                row.samples,
                _num(row.mean),
                _num(row.median),
                _num(row.p90),
                _num(row.p95),
                _num(row.p99),
                _num(row.min),
                _num(row.max),
                round(row.error_pct, 2),
                _num(row.throughput),
                "" if row.kb_per_s is None else _num(row.kb_per_s),
                "" if row.avg_bytes is None else round(row.avg_bytes),
            ]
        )

    _blank(writer)
    writer.writerow(["Response codes"])
    writer.writerow(["Code", "Count", "% of samples"])
    for code, count, share in status_rows(result):
        writer.writerow([code, count, round(share, 2)])

    if result.errors:
        _blank(writer)
        writer.writerow(["Errors"])
        writer.writerow(["Count", "Message"])
        for message, count in result.errors:
            writer.writerow([count, message])

    if result.timeline:
        _blank(writer)
        writer.writerow(["Throughput over time"])
        writer.writerow(
            ["From (s)", "To (s)", "Samples", "Failed", "Throughput (req/s)", "Average (ms)"]
        )
        for item in result.timeline:
            writer.writerow(
                [
                    item.start_s,
                    item.start_s + item.width_s,
                    item.requests,
                    item.failed,
                    _num(item.rps),
                    _num(item.mean_ms) if item.requests else "",
                ]
            )

    return buffer.getvalue()


def summary_pairs(result: LoadResult, generated: datetime | None = None) -> list[tuple[str, str]]:
    """The run's own details, as label/value pairs both exports show."""
    if generated is None:
        generated = datetime.now().astimezone()
    stop = (
        f"{result.limit} seconds"
        if result.stop_after == "seconds"
        else f"{result.limit:,} iterations"
    )
    pairs = [
        ("Test", result.name or "(unnamed)"),
        ("Target", result.target),
        ("Engine", result.engine),
        ("Environment", result.environment or "none"),
        ("Users", str(result.users)),
        ("Ramp-up", f"{result.ramp_up_s} s"),
        ("Stop after", stop),
        ("State", result.state),
        ("Duration", f"{result.elapsed_ms / 1000:.1f} s"),
        ("Samples", f"{result.requests:,}"),
        ("Failed", f"{result.failed:,} ({result.failed_pct:.2f}%)"),
        ("Throughput", f"{result.rps:.2f} req/s"),
        ("Generated", generated.strftime("%Y-%m-%d %H:%M:%S %Z").strip()),
    ]
    if result.target_kind == "scenario":
        pairs.insert(
            -1,
            (
                "Scenario passes",
                f"{result.iterations:,} ({result.iterations_failed:,} failed)",
            ),
        )
    return pairs


def _blank(writer) -> None:
    writer.writerow([])


def _num(value: float) -> float:
    """One decimal place below 100, whole numbers above.

    A mean of 12.3 ms and a mean of 1,284 ms both want to be read at a glance;
    only the first is helped by the decimal.
    """
    return round(value, 1) if value < 100 else round(value)


def filename(result: LoadResult, suffix: str, moment: datetime | None = None) -> str:
    """A download name that sorts by date and says which test it came from."""
    if moment is None:
        moment = datetime.now().astimezone()
    slug = re.sub(r"[^a-z0-9]+", "-", (result.name or "run").lower()).strip("-")
    return f"hitman-{slug or 'run'}-{moment:%Y%m%d-%H%M%S}.{suffix}"
