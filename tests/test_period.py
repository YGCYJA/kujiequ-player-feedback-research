import json
from pathlib import Path

import pytest

from kujiequ_research.period import filter_period


START = "2026-09-25T00:00:00+08:00"
END = "2026-10-25T15:00:01+08:00"


def row(comment_id="1", **changes):
    item = {
        "post_id": "100", "comment_id": comment_id, "text": "测试角色反馈",
        "created_at": "", "created_at_raw": "10-22", "time_source": "platform_display",
        "post_published_at": "2026-10-01T12:00:00+08:00",
        "collected_at": "2026-10-25T15:00:00+08:00", "dataset_kind": "live",
    }
    item.update(changes)
    return item


def run_filter(tmp_path, rows, start=START, end=END, posts=None):
    source = tmp_path / "source" / "comments.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows), encoding="utf-8")
    output = tmp_path / "selected" / "comments.jsonl"
    posts_path = None
    if posts is not None:
        posts_path = tmp_path / "posts.json"
        posts_path.write_text(json.dumps(posts), encoding="utf-8")
    summary = filter_period(source, output, start, end, posts_path)
    selected = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    return summary, selected


def test_exact_timestamps_use_half_open_window_and_are_not_replaced_by_display(tmp_path):
    original = "2026-09-24T16:00:00Z"  # Equal to inclusive local start.
    summary, selected = run_filter(tmp_path, [
        row("start", created_at=original, created_at_raw="10-22"),
        row("end", created_at=END),
        row("before", created_at="2026-09-24T23:59:59+08:00"),
    ])
    assert [item["comment_id"] for item in selected] == ["start"]
    assert selected[0]["created_at"] == original
    assert selected[0]["created_at_raw"] == "10-22"
    assert selected[0]["time_precision"] == "exact"
    assert summary["outside_period_rows"] == 2
    assert summary["before_window_rows"] == summary["after_window_rows"] == 1


def test_month_day_year_inference_is_bounded_and_audited(tmp_path):
    summary, selected = run_filter(tmp_path, [row()])
    assert selected[0]["created_at"] == "2026-10-22"
    assert selected[0]["time_source"] == "display_date_bounded_by_post"
    assert selected[0]["time_precision"] == "day"
    assert selected[0]["time_interval_start"] == "2026-10-22T00:00:00+08:00"
    assert selected[0]["time_interval_end"] == "2026-10-23T00:00:00+08:00"
    assert selected[0]["time_interval_end_inclusive"] is False
    assert summary["day_precision_inferred_rows"] == summary["kept_day_precision_inferred_rows"] == 1
    assert summary["kept_day_precision_rows"] == 1


def test_month_day_does_not_default_to_current_year_or_guess_multi_year(tmp_path):
    summary, selected = run_filter(tmp_path, [
        row("no-post", post_published_at=""),
        row("multiple", post_published_at="2024-10-01T00:00:00+08:00"),
        row("impossible", created_at_raw="09-20"),
        row("missing-collection", collected_at=""),
        row("relative", created_at_raw="昨天"),
    ])
    assert not selected
    assert summary["undetermined_date_rows"] == 5
    assert summary["reason_counts"]["month_day_multiple_possible_years"] == 1
    assert summary["reason_counts"]["month_day_no_possible_date"] == 1
    assert "测试角色反馈" not in json.dumps(summary, ensure_ascii=False)


def test_cross_year_and_leap_day_candidates_are_validated(tmp_path):
    summary, selected = run_filter(tmp_path, [
        row("previous-year", created_at_raw="12-31", post_published_at="2025-12-20T00:00:00+08:00", collected_at="2026-01-05T00:00:00+08:00"),
        row("invalid-date", created_at_raw="02-30"),
    ], start="2025-12-01T00:00:00+08:00", end="2026-02-01T00:00:00+08:00")
    assert selected[0]["created_at"] == "2025-12-31"
    assert summary["undetermined_date_rows"] == 1


def test_today_interval_uses_collection_upper_bound_without_inventing_time(tmp_path):
    summary, selected = run_filter(tmp_path, [row(created_at_raw="10-25")])
    item = selected[0]
    assert item["created_at"] == "2026-10-25"
    assert item["time_interval_end"] == "2026-10-25T15:00:00+08:00"
    assert item["time_interval_end_inclusive"] is True
    assert summary["kept_rows"] == 1
    # If the half-open end equals collection, the remaining boundary instant is
    # uncertain, so conservatively exclude rather than round the comment time.
    other, selected = run_filter(tmp_path, [row(created_at_raw="10-25")], end="2026-10-25T15:00:00+08:00")
    assert not selected
    assert other["boundary_overlap_rows"] == 1


def test_day_interval_intersects_post_bound_and_excludes_partial_start_overlap(tmp_path):
    summary, selected = run_filter(tmp_path, [
        row("bounded", created_at="2026-09-25", post_published_at="2026-09-25T12:00:00+08:00"),
        row("overlap", created_at="2026-09-25", post_published_at="2026-09-20T00:00:00+08:00"),
    ], start="2026-09-25T10:00:00+08:00")
    assert [item["comment_id"] for item in selected] == ["bounded"]
    assert selected[0]["time_interval_start"] == "2026-09-25T12:00:00+08:00"
    assert summary["boundary_overlap_rows"] == 1


def test_full_date_needs_no_year_inference_and_can_use_date_only_window(tmp_path):
    summary, selected = run_filter(tmp_path, [
        row(created_at="2026-09-25", post_published_at="", collected_at=""),
    ], start="2026-09-25", end="2026-09-25")
    assert selected[0]["created_at"] == "2026-09-25"
    assert summary["start"] == "2026-09-25T00:00:00+08:00"
    assert summary["end"] == "2026-09-26T00:00:00+08:00"
    assert summary["day_precision_inferred_rows"] == 0


def test_inconsistent_bounds_and_naive_timestamp_are_excluded(tmp_path):
    summary, selected = run_filter(tmp_path, [
        row("before-post", created_at="2026-09-30"),
        row("future", created_at="2026-10-26"),
        row("naive", created_at="2026-10-22T13:00:00"),
    ])
    assert not selected
    assert summary["undetermined_date_rows"] == 3
    assert summary["reason_counts"]["date_inconsistent_with_known_bounds"] == 2
    assert summary["reason_counts"]["invalid_created_at"] == 1


def test_post_metadata_and_manifest_are_copied_without_mutating_sources(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    manifest = {"dataset_kind": "live", "post_ids": ["100"], "requests": 4}
    marker = source_dir / "collection_manifest.json"
    marker.write_text(json.dumps(manifest), encoding="utf-8")
    summary, selected = run_filter(tmp_path, [row(post_published_at="")], posts={"posts": [{"post_id": "100", "published_at": "2026-10-01T00:00:00+08:00", "title": "test"}]})
    assert selected[0]["post_published_at"] == "2026-10-01T00:00:00+08:00"
    assert json.loads(marker.read_text()) == manifest
    copied = json.loads((tmp_path / "selected" / "collection_manifest.json").read_text())
    assert copied["requests"] == 4
    assert copied["period"]["end"] == END
    assert json.loads((tmp_path / "selected" / "period_summary.json").read_text()) == summary


def test_audit_outcomes_are_exhaustive_and_malformed_rows_do_not_leak(tmp_path):
    source = tmp_path / "comments.jsonl"
    source.write_text(json.dumps(row()) + "\n" + "{broken\n[]\n" + json.dumps(row("unknown", created_at_raw="3小时前")) + "\n", encoding="utf-8")
    summary = filter_period(source, tmp_path / "out" / "comments.jsonl", START, END)
    assert summary["input_rows"] == 4
    assert summary["malformed_rows"] == 2
    assert summary["input_rows"] == sum(summary[key] for key in ("kept_rows", "outside_period_rows", "undetermined_date_rows", "malformed_rows"))
    assert all("text" not in example for example in summary["audit_examples"])


def test_invalid_window_paths_and_conflicting_posts_are_errors(tmp_path):
    source = tmp_path / "comments.jsonl"
    source.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="different paths"):
        filter_period(source, source, START, END)
    with pytest.raises(ValueError, match="UTC offset"):
        filter_period(source, tmp_path / "out.jsonl", "2026-09-25T00:00:00", END)
    with pytest.raises(ValueError, match="earlier"):
        filter_period(source, tmp_path / "out.jsonl", END, START)
    posts = tmp_path / "posts.json"
    posts.write_text(json.dumps([{"post_id": "100", "published_at": "2026-01-01"}, {"post_id": "100", "published_at": "2026-02-01"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="conflicting"):
        filter_period(source, tmp_path / "out.jsonl", START, END, posts)
    with pytest.raises(FileNotFoundError):
        filter_period(tmp_path / "missing.jsonl", tmp_path / "out.jsonl", START, END)


def test_genuinely_empty_input_produces_empty_output(tmp_path):
    summary, selected = run_filter(tmp_path, [])
    assert summary["input_rows"] == summary["kept_rows"] == 0
    assert not selected


def test_all_malformed_input_fails_instead_of_claiming_empty_success(tmp_path):
    source, output = tmp_path / "comments.jsonl", tmp_path / "out" / "comments.jsonl"
    source.write_text("{broken\n[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="all input rows are malformed"):
        filter_period(source, output, START, END)
    assert not output.exists()
    assert not (output.parent / "period_summary.json").exists()
