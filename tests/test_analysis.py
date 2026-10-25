"""Research-relevant edge cases: overlapping labels, empty data and escaping."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from kujiequ_research.analysis import analyze_comments
from kujiequ_research.report import render_report


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        # Keep temporary fixtures under the explicitly writable workspace.
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.folder = Path(self.temp.name)
        self.source = self.folder / "comments.csv"
        self.output = self.folder / "report"

    def tearDown(self):
        self.temp.cleanup()

    def write_rows(self, rows):
        with self.source.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["post_id", "comment_id", "created_at", "text", "source_url", "dataset_kind"])
            writer.writeheader()
            writer.writerows(rows)

    def test_multilabel_denominator_and_no_sentiment_assumption(self):
        self.write_rows([
            {"text": "手感很好，没有卡顿", "created_at": "2026-10-24T18:00:00Z"},
            {"text": "这个技能的伤害需要讨论", "created_at": "2026-10-25T20:00:00+08:00"},
            {"text": "周末愉快", "created_at": "invalid"},
        ])
        summary = analyze_comments(self.source, self.output, split_date="2026-10-25")
        topics = {item["id"]: item for item in summary["topic_counts"]}
        self.assertEqual(summary["total_comments"], 3)
        self.assertEqual(summary["matched_comments"], 2)
        self.assertEqual(summary["multi_topic_comments"], 2)
        self.assertAlmostEqual(topics["performance_fps"]["share_of_all"], 1 / 3, places=5)
        # "没有卡顿" is a mention, never classified as a complaint.
        self.assertEqual(topics["performance_fps"]["comment_count"], 1)
        self.assertEqual(summary["methodology"]["sentiment"], "未执行")
        self.assertFalse(summary["human_review_completed"])
        self.assertEqual(summary["unknown_time_comments"], 1)
        self.assertEqual(summary["daily"][0]["date"], "2026-10-25")
        self.assertEqual(summary["split_comparison"]["before"]["total_comments"], 0)
        self.assertEqual(summary["split_comparison"]["after"]["total_comments"], 2)
        self.assertIn("不可比较", render_report(self.output).read_text(encoding="utf-8"))

    def test_empty_csv_produces_complete_report(self):
        self.write_rows([])
        summary = analyze_comments(self.source, self.output)
        self.assertEqual(summary["total_comments"], 0)
        self.assertEqual(summary["review_sample_size"], 0)
        self.assertEqual(summary["daily"], [])
        self.assertTrue(all(topic["share_of_all"] == 0 for topic in summary["topic_counts"]))
        for name in ["summary.json", "topics.csv", "daily_counts.csv", "labeled_comments.csv", "review_sample.csv", "key_findings.md"]:
            self.assertTrue((self.output / name).exists(), name)
        page = render_report(self.output).read_text(encoding="utf-8")
        self.assertIn("零样本报告", page)
        self.assertNotIn("NaN", page)

    def test_html_and_csv_are_safe_with_untrusted_comments(self):
        self.write_rows([
            {"comment_id": "1", "text": '<script>alert("卡顿")</script>', "source_url": 'javascript:alert("x")'},
            {"comment_id": "2", "text": '=HYPERLINK("https://example.invalid")', "source_url": 'https://example.invalid/?x=" onmouseover="bad'},
            {"comment_id": "3", "text": '  @SUM(1+1)'},
        ])
        analyze_comments(self.source, self.output)
        page = render_report(self.output).read_text(encoding="utf-8")
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn('href="javascript:', page)
        self.assertNotIn(' onmouseover="bad', page)
        with (self.output / "labeled_comments.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertTrue(rows[1]["text"].startswith("'="))
        self.assertTrue(rows[2]["text"].startswith("'  @"))

    def test_custom_dictionary_and_demo_provenance(self):
        self.write_rows([{"text": "今汐特效", "created_at": "2026-10-25 12:30:00"}])
        config = self.folder / "topics.json"
        config.write_text(json.dumps({"groups": {"character": "角色"}, "topics": [{"id": "custom", "name": "自定义", "group": "character", "keywords": ["今汐"]}]}), encoding="utf-8")
        self.output.mkdir()
        (self.output / "collection_manifest.json").write_text('{"dataset_kind":"demo"}', encoding="utf-8")
        summary = analyze_comments(self.source, self.output, config_path=config)
        self.assertEqual(summary["dataset_kind"], "demo")
        self.assertEqual(summary["topic_counts"][0]["comment_count"], 1)
        self.assertIn("合成演示", render_report(self.output).read_text(encoding="utf-8"))
        self.assertIn("不是实际玩家研究结论", " ".join(summary["limitations"]))

    def test_numeric_english_forms_do_not_match_unrelated_words(self):
        self.write_rows([
            {"text": "iOS18 iPhone16 可以选60FPS"},
            {"text": "shopping androids"},
        ])
        summary = analyze_comments(self.source, self.output)
        topics = {item["id"]: item for item in summary["topic_counts"]}
        self.assertEqual(topics["performance_fps"]["comment_count"], 1)
        self.assertEqual(topics["device_compatibility"]["comment_count"], 1)
        self.assertEqual(topics["network"]["comment_count"], 0)

    def test_live_manifest_cannot_hide_demo_or_mixed_rows(self):
        self.write_rows([{"text": "技能演示评论", "dataset_kind": "demo"}, {"text": "手感", "dataset_kind": "live"}])
        self.output.mkdir()
        (self.output / "collection_manifest.json").write_text('{"dataset_kind":"live"}', encoding="utf-8")
        summary = analyze_comments(self.source, self.output)
        self.assertEqual(summary["dataset_kind"], "mixed")
        page = render_report(self.output).read_text(encoding="utf-8")
        self.assertIn("来源混合或冲突", page)
        self.assertIn("可能包含演示内容", page)

    def test_readable_collection_scope_allowlist_and_partial_dates(self):
        self.write_rows([
            {"text": "技能", "created_at": "2026-10-25T10:00:00+08:00", "dataset_kind": "live"},
            {"text": "周末好", "created_at": "昨天", "dataset_kind": "live"},
        ])
        self.output.mkdir()
        manifest = {
            "dataset_kind": "live", "status": "limited",
            "headers": {"Authorization": "secret-header-value"},
            "scope": {"order": 3, "include_replies": True, "page_size": 5, "secret": "secret-scope-value"},
            "limits_per_post": {"comments": 20, "pages_per_invocation": 4, "secret": "secret-limit-value"},
            "posts": {
                "post-one": {"source_url": 'https://example.invalid/?x=" onclick="bad', "comments_saved": 16, "complete_within_scope": False, "stop_reason": "page_limit", "headers": "secret-post-value"},
                "post-two": {"source_url": "javascript:alert(1)", "comments_saved": 20, "complete_within_scope": False, "stop_reason": "comment_limit"},
            },
        }
        (self.output / "collection_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        analyze_comments(self.source, self.output)
        page = render_report(self.output).read_text(encoding="utf-8")
        for expected in ["达到采样上限，未采完整", "最新排序（3）", "包含楼中楼回复", "每帖评论上限", "20 条", "4 页", "post-one", "post-two", "达到本次页数上限", "达到评论条数上限", "日期覆盖：1 / 2 条（50.0%）", "图表仅覆盖有效日期样本"]:
            self.assertIn(expected, page)
        for excluded in ["secret-header-value", "secret-scope-value", "secret-limit-value", "secret-post-value", 'href="javascript:', ' onclick="bad']:
            self.assertNotIn(excluded, page)


if __name__ == "__main__":
    unittest.main()
