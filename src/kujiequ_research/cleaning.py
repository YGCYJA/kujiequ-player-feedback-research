"""Auditable, conservative cleaning of normalized public-comment JSONL.

Identifiers stay strings. Only the comment text is escaped for spreadsheet formula
safety at CSV-write time; the unescaped text is used for filtering. No user names,
IP addresses, or source user IDs are exported.
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


CSV_FIELDS = (
    "post_id", "comment_id", "parent_id", "root_id", "created_at",
    "created_at_raw", "time_source", "time_precision", "post_published_at",
    "time_interval_start", "time_interval_end", "time_interval_end_inclusive", "text",
    "like_count", "reply_count", "author_hash", "source_url", "collected_at",
    "dataset_kind",
)
_ID_FIELDS = ("post_id", "comment_id", "parent_id", "root_id")
_BREAK_TAGS = {"br", "p", "div", "li", "ul", "ol", "blockquote", "hr"}
_HIDDEN_TAGS = {"script", "style"}


class _CommentHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _HIDDEN_TAGS:
            self.hidden_depth += 1
        elif not self.hidden_depth and tag in _BREAK_TAGS:
            self.parts.append(" ")
        elif not self.hidden_depth and tag == "img":
            # Community emoji can be represented by img alt text.
            alt = dict(attrs).get("alt")
            if alt:
                self.parts.append(alt)

    def handle_endtag(self, tag: str) -> None:
        if tag in _HIDDEN_TAGS and self.hidden_depth:
            self.hidden_depth -= 1
        elif not self.hidden_depth and tag in _BREAK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def clean_text(value: str) -> str:
    """Remove markup/extra whitespace without removing negations or emoji."""
    parser = _CommentHTMLParser()
    # Decode entities before parsing so entity-encoded HTML is also removed.
    parser.feed(html.unescape(value))
    parser.close()
    text = html.unescape("".join(parser.parts))
    text = text.replace("\ufeff", "").replace("\u200b", "")
    return re.sub(r"\s+", " ", text).strip()


def _identifier(value: Any, field: str, required: bool = False) -> str:
    # Python int parsing is exact even for 19-digit IDs. Floats are rejected,
    # because converting an already-rounded floating-point ID cannot repair it.
    if isinstance(value, bool) or not isinstance(value, (str, int, type(None))):
        raise ValueError(f"{field} must be a string or an exact integer ID")
    result = "" if value is None else str(value)
    if required and not result.strip():
        raise ValueError(f"{field} is required and must not be blank")
    return result


def _normalize_row(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("each JSONL row must be an object")
    if not isinstance(item.get("text"), str):
        raise ValueError("text is required and must be a string")
    row: dict[str, Any] = {}
    for field in _ID_FIELDS:
        row[field] = _identifier(item.get(field), field, field in {"post_id", "comment_id"})
    row["text"] = clean_text(item["text"])
    for field in (
        "created_at", "created_at_raw", "time_source", "time_precision",
        "post_published_at", "time_interval_start", "time_interval_end",
        "author_hash", "source_url", "collected_at", "dataset_kind",
    ):
        value = item.get(field, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        row[field] = value
    inclusive = item.get("time_interval_end_inclusive", "")
    if inclusive is None:
        inclusive = ""
    if not isinstance(inclusive, bool) and inclusive != "":
        raise ValueError("time_interval_end_inclusive must be a boolean or empty")
    row["time_interval_end_inclusive"] = inclusive
    row["dataset_kind"] = row["dataset_kind"] or "unknown"
    for field in ("like_count", "reply_count"):
        value = item.get(field, "")
        if value is None or value == "":
            # An omitted count is unknown, not evidence of zero engagement.
            row[field] = ""
            continue
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a nonnegative integer")
        if isinstance(value, str) and re.fullmatch(r"\d+", value):
            value = int(value)
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must be a nonnegative integer")
        row[field] = value
    return row


def _is_too_short(text: str, min_length: int) -> bool:
    if len(text) >= min_length:
        return False
    # At the default threshold retain single-character Chinese feedback (卡/烫/
    # 不/差, etc.) and emoji. An explicitly higher threshold is respected.
    if min_length == 2 and len(text) == 1:
        return unicodedata.category(text) not in {"Lo", "So", "Sk"}
    return True


def _valid_timestamp(value: str) -> bool:
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            datetime.strptime(value, "%Y-%m-%d")
            return True  # Full calendar dates explicitly preserve day precision.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


def _kind(value: str) -> str:
    return value if value in {"live", "demo"} else "unknown"


def _atomic_write(path: Path, write: Any, encoding: str) -> None:
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding=encoding, newline="", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = handle.name
            write(handle)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def clean_comments(
    input_jsonl: Path,
    output_csv: Path,
    min_length: int = 2,
    include_keywords: list[str] | None = None,
) -> dict[str, Any]:
    """Clean JSONL and write a UTF-8-BOM CSV plus ``cleaning_summary.json``.

    Every physical input line gets exactly one outcome, including blank/invalid
    JSON lines as malformed. The first schema-valid occurrence of each
    ``(post_id, comment_id)`` is used; identical text under distinct IDs remains.
    Keyword filtering is case-insensitive OR matching on cleaned text. A file
    containing only malformed rows raises ValueError instead of claiming success.
    A genuinely empty file or a valid file with no retained rows is supported.

    The minimum is a Unicode code-point count; at the default of two, one-character
    non-Latin feedback and emoji are retained. A higher explicit minimum has no
    exception. No stopword, sentiment, spam, or alleged-bot filtering is applied.
    """
    input_jsonl, output_csv = Path(input_jsonl), Path(output_csv)
    if isinstance(min_length, bool) or not isinstance(min_length, int) or min_length < 0:
        raise ValueError("min_length must be a nonnegative integer")
    if include_keywords is not None and (
        not isinstance(include_keywords, list)
        or any(not isinstance(keyword, str) for keyword in include_keywords)
    ):
        raise ValueError("include_keywords must be a list of strings or None")
    keywords = list(dict.fromkeys(k.strip() for k in (include_keywords or []) if k.strip()))
    folded_keywords = [keyword.casefold() for keyword in keywords]
    if input_jsonl.resolve() == output_csv.resolve():
        raise ValueError("input_jsonl and output_csv must be different paths")

    summary: dict[str, Any] = {
        "input_rows": 0, "kept_rows": 0, "duplicate_rows": 0, "empty_rows": 0,
        "too_short_rows": 0, "keyword_excluded_rows": 0, "malformed_rows": 0,
        "valid_schema_rows": 0, "invalid_created_at_rows": 0,
        "formula_escaped_rows": 0, "min_length": min_length,
        "include_keywords": keywords, "keyword_match": "case-insensitive OR",
        "deduplication_key": ["post_id", "comment_id"],
        "deduplication_policy": "first schema-valid occurrence; text is never a deduplication key",
        "short_text_policy": "default min_length=2 retains single non-Latin letters and emoji",
        "formula_escape_policy": "prefix apostrophe to CSV text beginning with =, +, -, or @; filtering uses unescaped text",
        "invalid_created_at_policy": "retain original value; count among kept rows",
        "malformed_examples": [],
    }
    seen: set[tuple[str, str]] = set()
    kinds: Counter[str] = Counter()
    kept_kinds: Counter[str] = Counter()
    kept: list[dict[str, Any]] = []

    # Missing/undecodable inputs propagate an error and never create an empty
    # success result. utf-8-sig accepts JSONL with or without a BOM.
    with input_jsonl.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            summary["input_rows"] += 1
            try:
                item = json.loads(line)
                row = _normalize_row(item)
            except (json.JSONDecodeError, ValueError) as exc:
                summary["malformed_rows"] += 1
                if len(summary["malformed_examples"]) < 20:
                    reason = (
                        f"invalid JSON at column {exc.colno}"
                        if isinstance(exc, json.JSONDecodeError) else str(exc)
                    )
                    summary["malformed_examples"].append({"line": line_number, "reason": reason})
                continue
            summary["valid_schema_rows"] += 1
            kinds[_kind(row["dataset_kind"])] += 1
            key = (row["post_id"], row["comment_id"])
            if key in seen:
                summary["duplicate_rows"] += 1
                continue
            seen.add(key)
            if not row["text"]:
                summary["empty_rows"] += 1
                continue
            if _is_too_short(row["text"], min_length):
                summary["too_short_rows"] += 1
                continue
            if folded_keywords and not any(k in row["text"].casefold() for k in folded_keywords):
                summary["keyword_excluded_rows"] += 1
                continue
            if not _valid_timestamp(row["created_at"]):
                summary["invalid_created_at_rows"] += 1
            kept_kinds[_kind(row["dataset_kind"])] += 1
            if row["text"].startswith(("=", "+", "-", "@")):
                row["text"] = "'" + row["text"]
                summary["formula_escaped_rows"] += 1
            kept.append(row)

    if summary["input_rows"] and not summary["valid_schema_rows"]:
        raise ValueError(
            f"No schema-valid comments in {summary['input_rows']} input rows; "
            f"all {summary['malformed_rows']} rows are malformed. "
            f"Examples: {summary['malformed_examples']}"
        )
    summary["kept_rows"] = len(kept)
    summary["dataset_kind_counts"] = dict(sorted(kinds.items()))
    summary["kept_dataset_kind_counts"] = dict(sorted(kept_kinds.items()))
    summary["dataset_kind"] = next(iter(kinds)) if len(kinds) == 1 else ("mixed" if kinds else "unknown")
    summary["input_path"] = str(input_jsonl.resolve())
    summary["output_path"] = str(output_csv.resolve())
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()

    output_csv.parent.mkdir(parents=True, exist_ok=True)

    def write_csv(handle: Any) -> None:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(kept)

    _atomic_write(output_csv, write_csv, "utf-8-sig")
    _atomic_write(
        output_csv.parent / "cleaning_summary.json",
        lambda handle: json.dump(summary, handle, ensure_ascii=False, indent=2),
        "utf-8",
    )
    return summary
