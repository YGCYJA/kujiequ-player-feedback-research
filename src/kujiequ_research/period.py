"""Select comments whose supported time interval falls inside a study window.

An aware timestamp stays exact. A calendar date stays a calendar date: its
possible interval is intersected with the known post publication and collection
bounds, never assigned an invented clock time. A month/day display gets a year
only when exactly one date is possible between those same factual bounds.

``start`` and ``end`` form a half-open interval [start, end). ISO timestamps must
include a UTC offset. A date-only start means Shanghai midnight; a date-only end
includes that calendar day, i.e. midnight of the following day. Prefer explicit
offset-bearing timestamps for reproducible research. Relative displays are not
resolved because the platform's rounding convention is not established.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .cleaning import _atomic_write


SHANGHAI = timezone(timedelta(hours=8))
_FULL_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_MONTH_DAY = re.compile(r"(\d{1,2})[-/月](\d{1,2})日?")


def _aware(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return moment if moment.tzinfo is not None and moment.utcoffset() is not None else None


def _calendar_date(value: str) -> date | None:
    if not _FULL_DATE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _midnight(value: date) -> datetime:
    return datetime.combine(value, time(), SHANGHAI)


def _window_bound(value: str, *, end: bool) -> datetime:
    if not isinstance(value, str):
        raise ValueError("start and end must be ISO strings")
    day = _calendar_date(value)
    if day is not None:
        try:
            return _midnight(day + timedelta(days=1) if end else day)
        except OverflowError as exc:
            raise ValueError("end calendar date is too large") from exc
    moment = _aware(value)
    if moment is None:
        raise ValueError("start/end must be complete dates or ISO timestamps with a UTC offset")
    return moment


def _publication(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    day = _calendar_date(value.strip())
    return _midnight(day) if day is not None else _aware(value.strip())


def _load_posts(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict) and "posts" in data:
        data = data["posts"]
    if isinstance(data, dict):
        data = [
            {"post_id": key, "published_at": value} if isinstance(value, str)
            else dict(value, post_id=key) if isinstance(value, dict) else value
            for key, value in data.items()
        ]
    if not isinstance(data, list):
        raise ValueError("posts_path must contain a list of posts or a post-ID mapping")
    result: dict[str, str] = {}
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("post_id"), (str, int)):
            raise ValueError("each post must have a post_id")
        key, value = str(item["post_id"]), item.get("published_at", "")
        if not isinstance(value, str):
            raise ValueError("post published_at must be a string")
        if key in result and result[key] != value:
            raise ValueError(f"conflicting publication dates for post {key}")
        result[key] = value
    return result


def _inferred_day(raw: str, published: datetime | None, collected: datetime | None) -> tuple[date | None, str]:
    match = _MONTH_DAY.fullmatch(raw.strip())
    if match is None:
        return None, "unsupported_or_missing_display_date"
    if published is None or collected is None:
        return None, "month_day_missing_publication_or_collection_bound"
    if published > collected:
        return None, "publication_after_collection"
    earliest = published.astimezone(SHANGHAI).date()
    latest = collected.astimezone(SHANGHAI).date()
    candidates: list[date] = []
    for year in range(earliest.year, latest.year + 1):
        try:
            candidate = date(year, int(match[1]), int(match[2]))
        except ValueError:
            continue
        if earliest <= candidate <= latest:
            candidates.append(candidate)
    if len(candidates) != 1:
        return None, "month_day_no_possible_date" if not candidates else "month_day_multiple_possible_years"
    return candidates[0], ""


def _classify(
    original: dict[str, Any], posts: dict[str, str], start: datetime, end: datetime,
) -> tuple[str, str, dict[str, Any], bool]:
    row = dict(original)
    for field in ("created_at", "created_at_raw", "time_source", "post_published_at", "collected_at"):
        if row.get(field) is None:
            row[field] = ""
        if field not in row:
            row[field] = ""
        if not isinstance(row[field], str):
            return "undetermined", "non_string_time_field", row, False
    raw = row["created_at_raw"]
    value = row["created_at"].strip()
    published_value = row["post_published_at"] or posts.get(str(row.get("post_id", "")), "")
    row["post_published_at"] = published_value
    published, collected = _publication(published_value), _aware(row["collected_at"])
    day = _calendar_date(value)
    inferred = False
    if value and day is None:
        moment = _aware(value)
        if moment is None:
            return "undetermined", "invalid_created_at", row, False
        row["time_precision"] = "exact"
        row["time_source"] = row["time_source"] or "provided_timestamp"
        # Leave created_at byte-for-byte as supplied; do not substitute raw MD.
        row["time_interval_start"] = moment.isoformat()
        row["time_interval_end"] = moment.isoformat()
        row["time_interval_end_inclusive"] = True
        return ("kept" if start <= moment < end else "outside"), "exact_timestamp", row, False
    if day is None:
        day = _calendar_date(raw.strip())
        if day is not None:
            row["time_source"] = row["time_source"] or "display_full_date"
        else:
            day, reason = _inferred_day(raw, published, collected)
            if day is None:
                return "undetermined", reason, row, False
            row["time_source"] = "display_date_bounded_by_post"
            inferred = True
        row["created_at"] = day.isoformat()
    else:
        row["time_source"] = row["time_source"] or "provided_full_date"
    row["time_precision"] = "day"
    try:
        lower, upper = _midnight(day), _midnight(day + timedelta(days=1))
    except OverflowError:
        return "undetermined", "date_overflow", row, inferred
    upper_inclusive = False
    if published is not None:
        lower = max(lower, published)
    if collected is not None and collected < upper:
        upper, upper_inclusive = collected, True
    if lower > upper or (lower == upper and not upper_inclusive):
        return "undetermined", "date_inconsistent_with_known_bounds", row, inferred
    row["time_interval_start"] = lower.isoformat()
    row["time_interval_end"] = upper.isoformat()
    row["time_interval_end_inclusive"] = upper_inclusive
    if lower >= end or upper < start or (upper == start and not upper_inclusive):
        return "outside", "day_interval_outside", row, inferred
    wholly_inside = lower >= start and (upper < end if upper_inclusive else upper <= end)
    if wholly_inside:
        return "kept", "bounded_day_interval", row, inferred
    return "undetermined", "day_interval_crosses_window_boundary", row, inferred


def filter_period(
    input_jsonl: Path, output_jsonl: Path, start: str, end: str,
    posts_path: Path | None = None,
) -> dict[str, Any]:
    """Write only provably in-window comments and an auditable summary.

    Month/day inference needs a known publication date and an offset-bearing
    collection timestamp. The inclusion decision covers the entire possible
    interval. A collection-clipped upper bound is inclusive; set the window end
    later than collection (e.g. one second) to include that last possible instant.
    Audit examples contain line/IDs/reasons, never comment text. An accompanying
    collection_manifest.json is copied with period metadata; the input manifest
    is left unchanged when output is in a different directory.
    """
    input_jsonl, output_jsonl = Path(input_jsonl), Path(output_jsonl)
    if input_jsonl.resolve() == output_jsonl.resolve():
        raise ValueError("input_jsonl and output_jsonl must be different paths")
    lower, upper = _window_bound(start, end=False), _window_bound(end, end=True)
    if lower >= upper:
        raise ValueError("start must be earlier than end")
    posts = _load_posts(posts_path)
    summary: dict[str, Any] = {
        "input_rows": 0, "kept_rows": 0, "outside_period_rows": 0,
        "undetermined_date_rows": 0, "malformed_rows": 0,
        "before_window_rows": 0, "after_window_rows": 0, "boundary_overlap_rows": 0,
        "day_precision_inferred_rows": 0, "kept_day_precision_inferred_rows": 0,
        "kept_day_precision_rows": 0, "kept_exact_timestamp_rows": 0,
        "window": {"start": lower.isoformat(), "end": upper.isoformat(), "interval": "[start, end)", "calendar_timezone": "Asia/Shanghai"},
        "start": lower.isoformat(), "end": upper.isoformat(),
        "date_policy": "retain exact timestamps; calendar-day intervals intersect known publication/collection bounds; include only wholly contained intervals",
        "month_day_policy": "infer year only from unique possible date bounded by publication and collection; never assume current year",
        "relative_date_policy": "exclude unverified relative displays",
        "audit_examples": [],
    }
    outcomes: Counter[str] = Counter()
    kept: list[dict[str, Any]] = []
    with input_jsonl.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            summary["input_rows"] += 1
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError("JSONL row is not an object")
            except (ValueError, json.JSONDecodeError):
                summary["malformed_rows"] += 1
                outcomes["malformed_json_or_non_object"] += 1
                if len(summary["audit_examples"]) < 30:
                    summary["audit_examples"].append({"line": line_number, "outcome": "malformed", "reason": "malformed_json_or_non_object"})
                continue
            outcome, reason, row, inferred = _classify(item, posts, lower, upper)
            outcomes[reason] += 1
            summary["day_precision_inferred_rows"] += int(inferred)
            if outcome == "kept":
                kept.append(row)
                is_day = row["time_precision"] == "day"
                summary["kept_day_precision_rows"] += int(is_day)
                summary["kept_exact_timestamp_rows"] += int(not is_day)
                summary["kept_day_precision_inferred_rows"] += int(inferred)
            else:
                summary["outside_period_rows" if outcome == "outside" else "undetermined_date_rows"] += 1
                if outcome == "outside":
                    interval_start = _aware(row["time_interval_start"])
                    summary["after_window_rows" if interval_start is not None and interval_start >= upper else "before_window_rows"] += 1
                summary["boundary_overlap_rows"] += int(reason == "day_interval_crosses_window_boundary")
                if len(summary["audit_examples"]) < 30:
                    summary["audit_examples"].append({
                        "line": line_number, "post_id": str(row.get("post_id", "")),
                        "comment_id": str(row.get("comment_id", "")),
                        "outcome": outcome, "reason": reason,
                    })
    if summary["input_rows"] and summary["malformed_rows"] == summary["input_rows"]:
        raise ValueError("all input rows are malformed; no valid comment objects to date-filter")
    summary["kept_rows"] = len(kept)
    summary["reason_counts"] = dict(sorted(outcomes.items()))
    summary["input_path"] = str(input_jsonl.resolve())
    summary["output_path"] = str(output_jsonl.resolve())
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path = input_jsonl.parent / "collection_manifest.json"
    manifest = None
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict):
            raise ValueError("collection_manifest.json must contain an object")
        manifest["period"] = dict(summary["window"], inclusion_policy=summary["date_policy"])
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(output_jsonl, lambda handle: handle.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in kept), "utf-8")
    _atomic_write(output_jsonl.parent / "period_summary.json", lambda handle: json.dump(summary, handle, ensure_ascii=False, indent=2), "utf-8")
    if manifest is not None:
        _atomic_write(output_jsonl.parent / "collection_manifest.json", lambda handle: json.dump(manifest, handle, ensure_ascii=False, indent=2), "utf-8")
    return summary
