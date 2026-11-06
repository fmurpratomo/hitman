"""Exporting a finished performance run: the aggregate table, CSV and print page."""

import csv
import io
from datetime import datetime, timezone

import pytest

from hitman.core.loadtest import LabelStats, LatencySummary, LoadResult, TimeSlice
from hitman.core.models import Request
from hitman.web.report import aggregate, filename, report_csv, status_rows, summary_pairs


def latency(mean=50.0, p50=48.0, p90=120.0, p95=180.0, p99=600.0, low=8.0, high=1800.0, count=100):
    return LatencySummary(
        count=count, mean=mean, min=low, p50=p50, p90=p90, p95=p95, p99=p99, max=high
    )


def make_result(**overrides):
    defaults = {
        "name": "Checkout flow",
        "target": "Scenario · Checkout",
        "target_kind": "scenario",
        "engine": "httpx",
        "environment": "staging",
        "users": 20,
        "ramp_up_s": 5,
        "stop_after": "seconds",
        "limit": 30,
        "state": "finished",
        "elapsed_ms": 30_000.0,
        "requests": 3000,
        "failed": 30,
        "bytes_received": 3_072_000,
        "latency": latency(count=3000),
        "statuses": [("200", 2970), ("500", 30)],
        "errors": [("Place order: status is 500", 30)],
        "labels": [
            LabelStats("Login", 1500, 0, latency(mean=40.0, count=1500), 1_024_000),
            LabelStats("Place order", 1500, 30, latency(mean=60.0, count=1500), 2_048_000),
        ],
        "iterations": 1500,
        "iterations_failed": 30,
        "timeline": [
            TimeSlice(start_s=index, width_s=1, requests=100, failed=1, mean_ms=50.0)
            for index in range(3)
        ],
    }
    return LoadResult(**{**defaults, **overrides})


def run_with(app, result):
    """A stored run, which is what the export routes work from."""
    return app.state.store.add_load_run(None, result)


def rows_of(text):
    return list(csv.reader(io.StringIO(text)))


def section(text, heading):
    """The rows under a section heading, up to the blank line that ends it."""
    rows = rows_of(text)
    start = next(i for i, row in enumerate(rows) if row and row[0] == heading)
    out = []
    for row in rows[start + 1 :]:
        if not row:
            break
        out.append(row)
    return out


# --- the aggregate table ------------------------------------------------


def test_every_label_gets_a_row_and_the_run_gets_a_total():
    rows = aggregate(make_result())
    assert [row.label for row in rows] == ["Login", "Place order", "TOTAL"]
    assert rows[-1].is_total is True
    assert rows[-1].samples == 3000


def test_a_single_label_run_still_gets_its_total_row():
    """JMeter keeps it, and it is where the eye goes first."""
    result = make_result(labels=[LabelStats("Ping", 10, 0, latency(count=10), 1024)])
    assert [row.label for row in aggregate(result)] == ["Ping", "TOTAL"]


def test_the_total_percentiles_come_from_the_run_not_from_the_rows():
    """Averaging percentiles is wrong; the run measured its own."""
    result = make_result(latency=latency(p95=999.0, count=3000))
    assert aggregate(result)[-1].p95 == 999.0


def test_error_percentage_is_per_label():
    login, order, total = aggregate(make_result())
    assert login.error_pct == 0.0
    assert order.error_pct == pytest.approx(2.0)
    assert total.error_pct == pytest.approx(1.0)


def test_throughput_is_samples_over_the_whole_run():
    """So the label rows add up to the total, as JMeter's do."""
    login, order, total = aggregate(make_result())
    assert login.throughput == pytest.approx(50.0)
    assert order.throughput == pytest.approx(50.0)
    assert total.throughput == pytest.approx(100.0)


def test_byte_columns_are_filled_in_when_sizes_were_measured():
    login, _order, total = aggregate(make_result())
    assert login.avg_bytes == pytest.approx(1_024_000 / 1500)
    assert total.kb_per_s == pytest.approx(3_072_000 / 1024 / 30)


def test_byte_columns_are_blank_for_a_run_that_never_measured_them():
    """A run stored before sizes were recorded must not read as zero bytes."""
    rows = aggregate(make_result(bytes_received=0))
    assert all(row.kb_per_s is None and row.avg_bytes is None for row in rows)


def test_a_run_of_no_length_does_not_divide_by_zero():
    rows = aggregate(make_result(elapsed_ms=0.0, requests=0, failed=0, labels=[]))
    assert rows[-1].throughput == 0.0
    assert rows[-1].error_pct == 0.0


def test_an_unnamed_label_is_named_rather_than_left_blank():
    result = make_result(labels=[LabelStats("", 5, 0, latency(count=5), 0)])
    assert aggregate(result)[0].label == "(unnamed)"


def test_status_rows_are_ordered_by_count_and_carry_a_share():
    codes = status_rows(make_result())
    assert codes[0][0] == "200"
    assert codes[0][2] == pytest.approx(99.0)
    assert codes[1][2] == pytest.approx(1.0)


# --- the CSV ------------------------------------------------------------


def test_the_csv_leads_with_the_aggregate_table():
    text = report_csv(make_result())
    header, login, order, total = section(text, "Aggregate report")
    assert header[0] == "Label" and header[-1] == "Avg. Bytes"
    assert login[0] == "Login" and order[0] == "Place order"
    assert total[0] == "TOTAL" and total[1] == "3000"


def test_csv_numbers_are_written_as_numbers():
    """So that sorting and charting in the spreadsheet work."""
    _header, _login, _order, total = section(report_csv(make_result()), "Aggregate report")
    # Error % without the sign, throughput without a thousands separator.
    assert total[9] == "1.0"
    assert float(total[10]) == pytest.approx(100.0)


def test_the_csv_carries_the_run_details_codes_errors_and_timeline():
    text = report_csv(make_result())
    assert "Hitman performance report" in text
    assert ["Code", "Count", "% of samples"] in rows_of(text)
    assert section(text, "Errors")[1] == ["30", "Place order: status is 500"]
    assert len(section(text, "Throughput over time")) == 4  # header plus three slices


def test_the_csv_omits_sections_with_nothing_in_them():
    text = report_csv(make_result(errors=[], timeline=[]))
    assert "Errors" not in text
    assert "Throughput over time" not in text


def test_the_byte_columns_are_empty_cells_when_not_measured():
    text = report_csv(make_result(bytes_received=0))
    _header, _login, _order, total = section(text, "Aggregate report")
    assert total[11] == "" and total[12] == ""


def test_the_csv_uses_crlf_so_every_spreadsheet_agrees():
    assert report_csv(make_result()).startswith("Hitman performance report\r\n")


def test_a_scenario_run_reports_its_passes():
    pairs = dict(summary_pairs(make_result()))
    assert pairs["Scenario passes"] == "1,500 (30 failed)"


def test_a_request_run_has_no_passes_line():
    assert "Scenario passes" not in dict(summary_pairs(make_result(target_kind="request")))


def test_the_download_name_carries_the_test_and_the_date():
    moment = datetime(2026, 3, 9, 14, 5, 2, tzinfo=timezone.utc)
    assert filename(make_result(), "csv", moment) == "hitman-checkout-flow-20260309-140502.csv"


def test_an_awkward_test_name_still_makes_a_filename():
    moment = datetime(2026, 3, 9, 14, 5, 2, tzinfo=timezone.utc)
    assert filename(make_result(name="!!!"), "csv", moment) == "hitman-run-20260309-140502.csv"
    assert filename(make_result(name=""), "csv", moment) == "hitman-run-20260309-140502.csv"


# --- the routes ---------------------------------------------------------


def test_the_csv_route_sends_a_download_with_a_byte_order_mark(client, app):
    run_id = run_with(app, make_result())
    reply = client.get(f"/performance/runs/{run_id}/export.csv")

    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/csv")
    assert "attachment" in reply.headers["content-disposition"]
    assert "hitman-checkout-flow-" in reply.headers["content-disposition"]
    # Without the BOM, Excel reads the file as the system's legacy encoding.
    assert reply.content.startswith(b"\xef\xbb\xbf")
    assert "TOTAL" in reply.text


def test_the_print_route_renders_the_whole_report(client, app):
    run_id = run_with(app, make_result())
    reply = client.get(f"/performance/runs/{run_id}/report")

    assert reply.status_code == 200
    assert "<!doctype html>" in reply.text.lower()
    assert "Aggregate report" in reply.text
    assert "Place order: status is 500" in reply.text
    # Self-contained: its styling has to survive being saved or mailed.
    assert "<style>" in reply.text
    assert "@media print" in reply.text


def test_the_print_route_names_the_test_in_the_title(client, app):
    run_id = run_with(app, make_result())
    assert "<title>Checkout flow — Hitman</title>" in client.get(
        f"/performance/runs/{run_id}/report"
    ).text


def test_a_response_body_cannot_escape_into_the_report(client, app):
    """The single most important test here: error text comes from the server."""
    result = make_result(errors=[("<script>alert(1)</script>", 3)])
    run_id = run_with(app, result)
    reply = client.get(f"/performance/runs/{run_id}/report")
    assert "<script>alert(1)</script>" not in reply.text
    assert "&lt;script&gt;" in reply.text


def test_exporting_a_run_that_is_not_there_is_a_404(client):
    assert client.get("/performance/runs/9999/export.csv").status_code == 404
    assert client.get("/performance/runs/9999/report").status_code == 404


def test_a_stored_run_offers_its_exports(client, app):
    run_id = run_with(app, make_result())
    reply = client.get(f"/performance/runs/{run_id}")
    assert f"/performance/runs/{run_id}/export.csv" in reply.text
    assert f"/performance/runs/{run_id}/report?print=1" in reply.text


def test_a_run_still_in_flight_offers_no_exports(client, app):
    """Half a test is not a report worth sending anyone."""
    app.state.store.save_request("Ping", Request(url="http://x.test/"))
    reply = client.post(
        "/performance/run",
        data={
            "load_name": "Ping",
            "load_description": "",
            "load_folder": "",
            "target": "request:1",
            "users": "1",
            "ramp_up_s": "0",
            "limit": "1",
            "stop_after": "iterations",
        },
    )
    assert reply.status_code == 200
    assert "export.csv" not in reply.text


def test_the_print_page_says_when_sizes_were_not_recorded(client, app):
    """An older run must not read as a server that returned nothing."""
    run_id = run_with(app, make_result(bytes_received=0))
    reply = client.get(f"/performance/runs/{run_id}/report")
    assert "Response sizes were not recorded for this run" in reply.text
