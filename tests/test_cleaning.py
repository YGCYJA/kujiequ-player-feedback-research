import csv
import json
from pathlib import Path

import pytest

from kujiequ_research.cleaning import CSV_FIELDS, clean_comments, clean_text


def comment(comment_id="1900000000000000001", **changes):
    result = {
        "post_id": "1900000000000000000", "comment_id": comment_id,
        "parent_id": "", "root_id": comment_id,
        "created_at": "2026-10-25T12:30:00+08:00", "text": "角色操作反馈很好",
        "like_count": 2, "reply_count": 0, "author_hash": "anon_abc",
        "source_url": "https://example.invalid/post", "collected_at": "2026-10-25T12:31:00+08:00",
        "dataset_kind": "demo",
    }
    result.update(changes)
    return result


def write_jsonl(path: Path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_ids_stay_exact_and_deduplication_uses_post_and_comment_ids(tmp_path):
    source, output = tmp_path / "comments.jsonl", tmp_path / "clean.csv"
    first = comment()
    write_jsonl(source, [
        first,
        comment(text="同一个ID的更新文本"),
        comment("1900000000000000002"),  # Identical feedback, another user/comment.
        comment(post_id="1900000000000000003"),  # Same comment ID, another post.
    ])
    summary = clean_comments(source, output)
    result = read_csv(output)
    assert len(result) == 3
    assert result[0]["post_id"] == "1900000000000000000"
    assert result[0]["comment_id"] == "1900000000000000001"
    assert result[0]["root_id"] == "1900000000000000001"
    assert result[0]["text"] == result[1]["text"]
    assert summary["duplicate_rows"] == 1
    assert summary["dataset_kind"] == "demo"
    assert output.read_bytes().startswith(b"\xef\xbb\xbf")


def test_html_negation_emoji_formula_and_private_fields(tmp_path):
    source, output = tmp_path / "comments.jsonl", tmp_path / "clean.csv"
    write_jsonl(source, [
        comment(text="<p>并不流畅&nbsp; 😢</p><script>secret</script><p>不推荐<img alt='[叹气]' src='x'></p>", username="private", user_id="123", ip="1.2.3.4"),
        comment("2", text="<p> =SUM(1,2) </p>"),
        comment("3", text="+角色"),
        comment("4", text="-角色"),
        comment("5", text="@角色"),
        comment("6", text="卡"),
        comment("7", text="不"),
        comment("8", text="👍"),
    ])
    summary = clean_comments(source, output)
    result = read_csv(output)
    assert result[0]["text"] == "并不流畅 😢 不推荐[叹气]"
    assert result[1]["text"] == "'=SUM(1,2)"
    assert [r["text"] for r in result[-3:]] == ["卡", "不", "👍"]
    assert summary["formula_escaped_rows"] == 4
    assert list(result[0]) == list(CSV_FIELDS)
    assert not {"username", "user_id", "ip"}.intersection(result[0])
    assert "private" not in output.read_text(encoding="utf-8-sig")
    assert clean_text("&lt;b&gt;不满意&lt;/b&gt;\n\t卡顿") == "不满意 卡顿"


def test_exclusive_audit_counts_keywords_and_invalid_timestamp(tmp_path):
    source, output = tmp_path / "comments.jsonl", tmp_path / "clean.csv"
    write_jsonl(source, [
        comment("1", text="角色很棒", created_at="bad-date"),
        comment("1", text="角色很棒"),
        comment("2", text="<p>&nbsp;</p>"),
        comment("3", text="a"),
        comment("4", text="剧情不错"),
        comment("5", text="优化问题", dataset_kind="live"),
        {"post_id": "bad", "text": "缺少评论ID"},
    ])
    with source.open("a", encoding="utf-8") as handle:
        handle.write("{invalid json}\n")
    summary = clean_comments(source, output, include_keywords=["角色", "优化", "角色", " "])
    expected = {
        "input_rows": 8, "kept_rows": 2, "duplicate_rows": 1, "empty_rows": 1,
        "too_short_rows": 1, "keyword_excluded_rows": 1, "malformed_rows": 2,
    }
    assert {k: summary[k] for k in expected} == expected
    assert summary["input_rows"] == sum(summary[k] for k in expected if k != "input_rows")
    assert summary["invalid_created_at_rows"] == 1
    assert read_csv(output)[0]["created_at"] == "bad-date"
    assert summary["include_keywords"] == ["角色", "优化"]
    assert summary["dataset_kind"] == "mixed"
    assert len(summary["malformed_examples"]) == 2
    assert json.loads((tmp_path / "cleaning_summary.json").read_text(encoding="utf-8")) == summary


@pytest.mark.parametrize("rows", [[], [comment(text=" ")], [comment(text="不匹配关键词")]])
def test_empty_results_have_header_and_summary(tmp_path, rows):
    source, output = tmp_path / "comments.jsonl", tmp_path / "clean.csv"
    write_jsonl(source, rows)
    summary = clean_comments(source, output, include_keywords=["优化"])
    assert summary["kept_rows"] == 0
    assert read_csv(output) == []
    with output.open(encoding="utf-8-sig", newline="") as handle:
        assert next(csv.reader(handle)) == list(CSV_FIELDS)
    assert (tmp_path / "cleaning_summary.json").is_file()


def test_missing_input_and_all_malformed_input_are_errors(tmp_path):
    source, output = tmp_path / "comments.jsonl", tmp_path / "clean.csv"
    with pytest.raises(FileNotFoundError):
        clean_comments(source, output)
    assert not output.exists()
    write_jsonl(source, [{"text": "没有ID"}, ["not an object"], comment(comment_id=1.9e18)])
    with pytest.raises(ValueError, match="all 3 rows are malformed"):
        clean_comments(source, output)
    assert not output.exists()


def test_integer_ids_are_exact_but_floats_are_rejected(tmp_path):
    source, output = tmp_path / "comments.jsonl", tmp_path / "clean.csv"
    write_jsonl(source, [
        comment(comment_id=1900000000000000001),
        comment(comment_id=1.9e18),
    ])
    summary = clean_comments(source, output)
    assert read_csv(output)[0]["comment_id"] == "1900000000000000001"
    assert summary["malformed_rows"] == 1


def test_keyword_matching_happens_before_formula_escape_and_is_case_insensitive(tmp_path):
    source, output = tmp_path / "comments.jsonl", tmp_path / "clean.csv"
    write_jsonl(source, [comment(text="=GPU优化")])
    summary = clean_comments(source, output, include_keywords=["gpu"])
    assert summary["kept_rows"] == 1
    assert read_csv(output)[0]["text"] == "'=GPU优化"


def test_explicit_higher_minimum_is_respected(tmp_path):
    source, output = tmp_path / "comments.jsonl", tmp_path / "clean.csv"
    write_jsonl(source, [comment(text="卡"), comment("2", text="不流畅")])
    summary = clean_comments(source, output, min_length=3)
    assert summary["too_short_rows"] == 1
    assert read_csv(output)[0]["text"] == "不流畅"


def test_unknown_engagement_counts_are_not_fabricated_as_zero(tmp_path):
    source, output = tmp_path / "comments.jsonl", tmp_path / "clean.csv"
    write_jsonl(source, [
        comment(like_count="", reply_count=None),
        comment("2", like_count=0, reply_count="0"),
    ])
    clean_comments(source, output)
    result = read_csv(output)
    assert result[0]["like_count"] == result[0]["reply_count"] == ""
    assert result[1]["like_count"] == result[1]["reply_count"] == "0"


def test_day_precision_and_time_provenance_survive_cleaning(tmp_path):
    source, output = tmp_path / "comments.jsonl", tmp_path / "clean.csv"
    write_jsonl(source, [comment(
        created_at="2026-10-25", created_at_raw="10-25",
        time_source="display_date_bounded_by_post", time_precision="day",
        post_published_at="2026-10-01T12:00:00+08:00",
        time_interval_start="2026-10-25T00:00:00+08:00",
        time_interval_end="2026-10-25T12:31:00+08:00",
        time_interval_end_inclusive=True,
    )])
    summary = clean_comments(source, output)
    result = read_csv(output)[0]
    assert result["created_at"] == "2026-10-25"
    assert result["created_at_raw"] == "10-25"
    assert result["time_source"] == "display_date_bounded_by_post"
    assert result["time_precision"] == "day"
    assert result["time_interval_end_inclusive"] == "True"
    assert summary["invalid_created_at_rows"] == 0
