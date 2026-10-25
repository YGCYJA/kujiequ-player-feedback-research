"""Date-filter a collected monthly study and export aggregate-only results."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kujiequ_research.cli import make_pipeline
from kujiequ_research.collector import write_json
from kujiequ_research.period import filter_period
from kujiequ_research.public_export import export_public_study


def finalize(study_path: Path, findings_path: Path | None = None) -> dict:
    study = json.loads(study_path.read_text(encoding="utf-8"))
    directory = ROOT / "data" / study["study_id"]
    output = ROOT / "output" / study["study_id"]
    public = ROOT / "research" / study["study_id"]
    manifest = json.loads((directory / "collection_manifest.json").read_text(encoding="utf-8"))
    metadata = json.loads((directory / "posts.json").read_text(encoding="utf-8"))
    window = manifest["period"]
    period = filter_period(directory / "comments.jsonl", directory / "window/comments.jsonl", window["start"], window["end"], posts_path=directory / "posts.json")
    results = make_pipeline(directory / "window/comments.jsonl", output)
    shutil.copyfile(directory / "window/period_summary.json", output / "period_summary.json")
    with (output / "cleaned_comments.csv").open(encoding="utf-8-sig", newline="") as stream:
        counts = Counter(row["post_id"] for row in csv.DictReader(stream))
    recommendations = json.loads(findings_path.read_text(encoding="utf-8")) if findings_path else {
        "review_method": "尚未完成语境核查；本版本只展示统计。", "reviewed_comments": 0, "findings": []}
    recommendations["source_summary"] = [{
        "source_url": post["source_url"], "title": post["title"], "published_at": post["published_at"],
        "collected_comments": manifest["posts"].get(post["post_id"], {}).get("comments_saved", 0),
        "included_comments": counts[post["post_id"]],
    } for post in metadata["posts"]]
    write_json(output / "recommendations.json", recommendations)
    result = export_public_study(output, public, title="鸣潮近一个月角色与优化反馈研究")
    audit = {
        "study_id": study["study_id"], "start": window["start"], "end": window["end"],
        "collected_comments": manifest["total_comments"], "date_included": period["kept_rows"],
        "date_undetermined": period["undetermined_date_rows"], "outside_window": period["outside_period_rows"],
        "analyzed_comments": result["total_comments"], "topic_matched": result["matched_comments"],
        "snapshot_sha256": hashlib.sha256((directory / "comments.jsonl").read_bytes()).hexdigest(),
        "public_report": str(public / "index.html"),
    }
    write_json(output / "study_audit.json", audit)
    return audit


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, default=ROOT / "config/month-study.json")
    parser.add_argument("--findings", type=Path)
    args = parser.parse_args()
    print(json.dumps(finalize(args.study, args.findings), ensure_ascii=False, indent=2))
