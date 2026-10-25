"""Public artifacts must remain useful without publishing row-level records."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from kujiequ_research.public_export import PUBLIC_FILES, export_public_study


class PublicExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.base = Path(self.temp.name)
        self.source, self.dest = self.base / "private", self.base / "public"
        self.source.mkdir()
        self.write("summary.json", {
            "dataset_kind": "live", "total_comments": 10, "matched_comments": 4,
            "unknown_time_comments": 1,
            "date_range": {"start": "2026-09-25", "end": "2026-10-25"},
            "topic_counts": [{"id": "fps", "name": "帧率与卡顿", "group": "optimization", "comment_count": 3, "share_of_all": .999}],
            "daily": [{"date": "2026-10-25", "comment_count": 9}],
        })

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        (self.source / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def public_text(self):
        return "\n".join(path.read_text(encoding="utf-8-sig") for path in self.dest.iterdir())

    def test_allowlist_excludes_nested_metadata_and_row_level_files(self):
        secret = "DO_NOT_PUBLISH_MARKER"
        summary = json.loads((self.source / "summary.json").read_text(encoding="utf-8"))
        summary.update({"input_file": "C:\\Users\\private\\raw.csv", "review_sample": [{"text": secret, "author_hash": secret}], "metadata": {"token": secret}, "limitations": [secret]})
        summary["topic_counts"][0]["raw_examples"] = [secret]
        self.write("summary.json", summary)
        self.write("collection_manifest.json", {"headers": {"Authorization": secret}, "private_salt": secret, "scope": {"input_path": secret}})
        self.write("cleaning_summary.json", {"input_rows": 12, "input_path": secret, "errors": [{"raw_comment": secret}]})
        (self.source / "labeled_comments.csv").write_text(secret, encoding="utf-8")
        (self.source / "review_sample.csv").write_text(secret, encoding="utf-8")
        result = export_public_study(self.source, self.dest)
        self.assertEqual({p.name for p in self.dest.iterdir()}, PUBLIC_FILES)
        self.assertNotIn(secret, self.public_text())
        self.assertNotIn("author_hash", json.dumps(result))
        self.assertNotIn("input_file", result)
        self.assertEqual(result["cleaning_counts"], {"input_rows": 12})

    def test_sources_allow_only_https_kurobbs_numeric_post_urls(self):
        urls = ["https://www.kurobbs.com/mc/post/123?token=SECRET#frag", "https://kurobbs.com/post/456/", "http://www.kurobbs.com/mc/post/1", "https://evil.com/mc/post/2", "https://www.kurobbs.com.evil.com/mc/post/3", "https://name@www.kurobbs.com/mc/post/4", "https://www.kurobbs.com:443/mc/post/5", "https://www.kurobbs.com/mc/post/not-numeric", "javascript:alert(1)"]
        self.write("recommendations.json", {"source_summary": [{"source_url": url, "title": "官方公告", "collected_comments": 8} for url in urls]})
        result = export_public_study(self.source, self.dest)
        self.assertEqual([s["source_url"] for s in result["sources"]], ["https://www.kurobbs.com/mc/post/123", "https://kurobbs.com/post/456"])
        self.assertNotIn("SECRET", self.public_text())

    def test_html_escaping_and_csv_formula_protection(self):
        self.write("recommendations.json", {"source_summary": [{"source_url": "https://www.kurobbs.com/mc/post/123", "title": '=HYPERLINK("bad")<script>alert(1)</script>', "published_at": "2026-10-20T12:00:00+08:00"}], "findings": [{"title": "<script>alert(2)</script>", "observation": '<img src=x onerror="alert(3)">'}]})
        export_public_study(self.source, self.dest)
        report = (self.dest / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", report)
        self.assertIn("&lt;script&gt;", report)
        self.assertNotIn("<img src=x", report)
        with (self.dest / "sources.csv").open(encoding="utf-8-sig", newline="") as stream:
            row = next(csv.DictReader(stream))
        self.assertTrue(row["title"].startswith("'="))
        self.assertIn("Content-Security-Policy", report)

    def test_findings_separate_observation_action_validation_and_counts(self):
        self.write("recommendations.json", {"review_method": "评论语境分析与归纳", "reviewed_comments": 8, "findings": [{"category": "optimization", "title": "性能线索", "observation": "部分样本提及特定场景卡顿", "recommendation": "记录设备与场景并复现", "validation": "开展帧时间测试", "evidence_count": 3, "source_count": 99, "source_urls": ["https://www.kurobbs.com/mc/post/123", "https://www.kurobbs.com/mc/post/123", "https://evil.com/post/1"], "caveat": "来源集中", "raw_comments": ["PRIVATE"]}]})
        result = export_public_study(self.source, self.dest)
        finding = result["findings"][0]
        self.assertEqual(finding["evidence_count"], 3)
        self.assertEqual(finding["source_count"], 1)
        self.assertNotIn("raw_comments", finding)
        self.assertNotIn("PRIVATE", self.public_text())
        report = (self.dest / "index.html").read_text(encoding="utf-8")
        for heading in ["观察发现", "建议", "待验证", "来源集中"]:
            self.assertIn(heading, report)
        self.assertAlmostEqual(result["topic_counts"][0]["share_of_all"], .3)

    def test_empty_sample_has_no_unsupported_conclusion(self):
        self.write("summary.json", {"dataset_kind": "live", "total_comments": 0, "topic_counts": [], "daily": []})
        result = export_public_study(self.source, self.dest)
        self.assertEqual(result["findings"], [])
        self.assertIn("零样本", (self.dest / "index.html").read_text(encoding="utf-8"))
        self.assertIn("尚未提供", (self.dest / "README.md").read_text(encoding="utf-8"))
        self.assertEqual(result["date_range"], {"start": None, "end": None})

    def test_rejects_mixed_public_directory_and_same_directory(self):
        self.dest.mkdir()
        raw = self.dest / "comments.jsonl"
        raw.write_text("private", encoding="utf-8")
        with self.assertRaises(ValueError):
            export_public_study(self.source, self.dest)
        self.assertEqual(raw.read_text(encoding="utf-8"), "private")
        with self.assertRaises(ValueError):
            export_public_study(self.source, self.source)

    def test_period_whitelist_and_sensitive_prose(self):
        self.write("period_summary.json", {"start": "2026-09-25", "end": "2026-10-25", "input_rows": 14, "kept_rows": 10, "outside_period_rows": 3, "undetermined_date_rows": 1, "input_path": "C:\\Users\\SECRET\\raw.csv", "unexpected": {"token": "PRIVATE"}})
        self.write("recommendations.json", {"review_method": "token=PRIVATE", "findings": [{"observation": "C:\\Users\\SECRET\\private.csv", "recommendation": "保存设备与触发场景", "caveat": "样本集中"}]})
        result = export_public_study(self.source, self.dest)
        self.assertEqual(result["study_window"], {"start": "2026-09-25", "end": "2026-10-25"})
        self.assertEqual(result["period_counts"]["kept_rows"], 10)
        self.assertNotIn("PRIVATE", self.public_text())
        self.assertNotIn("SECRET", self.public_text())
        self.assertIn("未公开", result["findings"][0]["observation"])

    def test_missing_summary_and_conflicting_demo_origin(self):
        (self.source / "summary.json").unlink()
        with self.assertRaises(ValueError):
            export_public_study(self.source, self.dest)
        self.write("summary.json", {"dataset_kind": "live", "total_comments": 1})
        self.write("cleaning_summary.json", {"dataset_kind": "demo"})
        result = export_public_study(self.source, self.dest)
        self.assertEqual(result["dataset_kind"], "mixed")
        self.assertIn("不能作为实际玩家研究结论", result["limitations"][0])

    def test_iso_window_preserves_offsets_and_discloses_day_inference(self):
        start, end = "2026-09-25T00:00:00+08:00", "2026-10-25T18:34:12.123456+08:00"
        self.write("period_summary.json", {"start": start, "end": end, "kept_rows": 10,
            "day_precision_inferred_rows": 12, "kept_day_precision_inferred_rows": 10,
            "kept_day_precision_rows": 10, "kept_exact_timestamp_rows": 0,
            "undetermined_date_rows": 2, "audit_examples": [{"comment_id": "PRIVATE"}]})
        result = export_public_study(self.source, self.dest)
        self.assertEqual(result["study_window"], {"start": start, "end": end})
        self.assertEqual(result["period_counts"]["kept_day_precision_inferred_rows"], 10)
        self.assertEqual(result["period_counts"]["kept_exact_timestamp_rows"], 0)
        self.assertIn("全部为日精度", result["methodology"]["date_precision"])
        for name in ["index.html", "README.md"]:
            text = (self.dest / name).read_text(encoding="utf-8")
            for expected in [start, end, "保留的年份推断记录（日精度）", "以官方栏目发布时间为公开评论下界的假设，不是底层创建时间官方保证", "不是评论精确创建时刻"]:
                self.assertIn(expected, text)
            self.assertNotIn("PRIVATE", text)


if __name__ == "__main__":
    unittest.main()
