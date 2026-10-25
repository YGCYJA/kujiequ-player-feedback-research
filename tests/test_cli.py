import json

from kujiequ_research.cli import main


def test_demo_end_to_end_marks_synthetic_and_writes_artifacts(tmp_path):
    output = tmp_path / "demo"
    assert main(["demo", "--output", str(output)]) == 0
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["dataset_kind"] == "demo"
    assert summary["total_comments"] == 24
    assert (output / "review_sample.csv").is_file()
    assert "合成演示数据" in (output / "report.html").read_text(encoding="utf-8")


def test_demo_cannot_overwrite_live_manifest(tmp_path):
    output = tmp_path / "live"
    output.mkdir()
    marker = output / "collection_manifest.json"
    marker.write_text('{"dataset_kind":"live"}', encoding="utf-8")
    assert main(["demo", "--output", str(output)]) == 2
    assert marker.read_text(encoding="utf-8") == '{"dataset_kind":"live"}'


def test_missing_data_does_not_create_fake_report(tmp_path):
    output = tmp_path / "out"
    assert main(["pipeline", "--data", str(tmp_path / "missing"), "--output", str(output)]) == 2
    assert not (output / "report.html").exists()


def test_period_command_writes_filtered_data_and_discloses_day_precision(tmp_path, capsys):
    source = tmp_path / "comments.jsonl"
    rows = [
        {"post_id": "100", "comment_id": "1", "text": "角色反馈", "created_at": "", "created_at_raw": "10-22", "collected_at": "2026-10-25T15:00:00+08:00"},
        {"post_id": "100", "comment_id": "2", "text": "优化反馈", "created_at": "", "created_at_raw": "昨天", "collected_at": "2026-10-25T15:00:00+08:00"},
    ]
    source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    posts = tmp_path / "posts.json"
    posts.write_text(json.dumps({"posts": [{"post_id": "100", "published_at": "2026-10-01T00:00:00+08:00"}]}), encoding="utf-8")
    output = tmp_path / "selected" / "comments.jsonl"
    assert main(["period", "--input", str(source), "--output", str(output), "--start", "2026-09-25T00:00:00+08:00", "--end", "2026-10-25T15:00:01+08:00", "--posts-metadata", str(posts)]) == 0
    selected = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(selected) == 1
    assert selected[0]["created_at"] == "2026-10-22"
    assert selected[0]["time_precision"] == "day"
    summary = json.loads((output.parent / "period_summary.json").read_text(encoding="utf-8"))
    assert summary["kept_day_precision_inferred_rows"] == 1
    assert summary["undetermined_date_rows"] == 1
    assert "年份推断 1 条" in capsys.readouterr().out
    assert not (output.parent / "report.html").exists()
    assert not (output.parent / "index.html").exists()


def test_period_failure_does_not_create_fake_data_or_report(tmp_path, capsys):
    source = tmp_path / "comments.jsonl"
    source.write_text("", encoding="utf-8")
    output = tmp_path / "selected" / "comments.jsonl"
    assert main(["period", "--input", str(source), "--output", str(output), "--start", "2026-10-25T00:00:00", "--end", "2026-10-25T15:00:00+08:00"]) == 2
    assert "未完成" in capsys.readouterr().err
    assert not output.exists()
    assert not (output.parent / "period_summary.json").exists()
    assert not (output.parent / "report.html").exists()
    assert not (output.parent / "index.html").exists()
    assert main(["period", "--input", str(tmp_path / "missing.jsonl"), "--output", str(output), "--start", "2026-09-25T00:00:00+08:00", "--end", "2026-10-25T15:00:00+08:00"]) == 2
    assert not output.exists()
    source.write_text("{broken\n", encoding="utf-8")
    assert main(["period", "--input", str(source), "--output", str(output), "--start", "2026-09-25T00:00:00+08:00", "--end", "2026-10-25T15:00:00+08:00"]) == 2
    assert not output.exists()
    assert not (output.parent / "report.html").exists()


def test_export_public_command_writes_only_aggregate_artifacts(tmp_path, capsys):
    source, output = tmp_path / "private", tmp_path / "public"
    source.mkdir()
    summary = {"dataset_kind": "live", "total_comments": 3, "topic_counts": [], "daily": []}
    (source / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (source / "period_summary.json").write_text(json.dumps({"kept_rows": 3, "kept_day_precision_rows": 3, "kept_day_precision_inferred_rows": 3, "kept_exact_timestamp_rows": 0}), encoding="utf-8")
    raw = source / "comments.jsonl"
    raw.write_text("PRIVATE_COMMENT_MARKER", encoding="utf-8")
    assert main(["export-public", "--input-dir", str(source), "--output-dir", str(output), "--title", "角色与优化研究"]) == 0
    public = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert public["title"] == "角色与优化研究"
    assert public["total_comments"] == 3
    assert public["period_counts"]["kept_day_precision_inferred_rows"] == 3
    assert "全部为日精度" in public["methodology"]["date_precision"]
    assert {path.name for path in output.iterdir()} == {"summary.json", "topics.csv", "daily_counts.csv", "sources.csv", "index.html", "README.md"}
    assert "PRIVATE_COMMENT_MARKER" not in "".join(path.read_text(encoding="utf-8-sig") for path in output.iterdir())
    assert raw.read_text(encoding="utf-8") == "PRIVATE_COMMENT_MARKER"
    assert "3 条纳入评论" in capsys.readouterr().out


def test_export_public_failure_does_not_create_fake_report(tmp_path):
    output = tmp_path / "public"
    assert main(["export-public", "--input-dir", str(tmp_path / "missing"), "--output-dir", str(output)]) == 2
    assert not (output / "index.html").exists()
    assert not (output / "summary.json").exists()


def test_pipeline_copies_date_audit_and_same_directory_clean_keeps_it(tmp_path):
    source, output = tmp_path / "demo", tmp_path / "analysis"
    assert main(["demo", "--output", str(source)]) == 0
    (source / "demo_comments.jsonl").rename(source / "comments.jsonl")
    period = {"kept_rows": 24, "kept_day_precision_rows": 24, "kept_day_precision_inferred_rows": 24, "kept_exact_timestamp_rows": 0}
    audit = source / "period_summary.json"
    audit.write_text(json.dumps(period), encoding="utf-8")
    assert main(["clean", "--input", str(source / "comments.jsonl"), "--output", str(source / "again.csv")]) == 0
    assert json.loads(audit.read_text(encoding="utf-8")) == period
    assert main(["pipeline", "--data", str(source), "--output", str(output)]) == 0
    assert json.loads((output / "period_summary.json").read_text(encoding="utf-8")) == period


def test_origin_conflict_does_not_overwrite_period_audit(tmp_path):
    source, output = tmp_path / "source", tmp_path / "out"
    source.mkdir()
    output.mkdir()
    (source / "collection_manifest.json").write_text('{"dataset_kind":"demo"}', encoding="utf-8")
    (source / "period_summary.json").write_text('{"kept_rows":99}', encoding="utf-8")
    (source / "comments.jsonl").write_text("", encoding="utf-8")
    (output / "collection_manifest.json").write_text('{"dataset_kind":"live"}', encoding="utf-8")
    audit = output / "period_summary.json"
    audit.write_text('{"kept_rows":3}', encoding="utf-8")
    assert main(["clean", "--input", str(source / "comments.jsonl"), "--output", str(output / "clean.csv")]) == 2
    assert audit.read_text(encoding="utf-8") == '{"kept_rows":3}'
    assert not (output / "clean.csv").exists()
