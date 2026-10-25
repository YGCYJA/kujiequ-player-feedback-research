"""Reproduce a bounded, auditable monthly public-comment study."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kujiequ_research.client import KuroClient
from kujiequ_research.collector import collect_comments, write_json
from kujiequ_research.models import PostTarget, ResearchError, normalize_time, now_iso


def discover(client: KuroClient, study: dict, directory: Path) -> dict:
    wanted = {item["post_id"]: item for item in study["posts"]}
    cached = directory / "posts.json"
    if cached.exists():
        obj = json.loads(cached.read_text(encoding="utf-8"))
        if {item["post_id"] for item in obj.get("posts", [])} == set(wanted):
            return obj
    # Read fixed page ranges: these official lists are NOT strictly date-sorted.
    discovered = {}
    for event_type, pages in ((2, 5), (3, 3)):
        for page in range(1, pages + 1):
            for post in client.announcements(3, page=page, page_size=30, event_type=event_type):
                post_id = str(post.get("postId", ""))
                if post_id in wanted:
                    discovered[post_id] = {
                        **wanted[post_id], "title": str(post.get("postTitle", "")),
                        "published_at": normalize_time(post.get("firstPublishTime") or post.get("publishTime")),
                        "listed_publish_time": normalize_time(post.get("publishTime")),
                        "published_at_basis": "official_firstPublishTime_or_publishTime; public_listing_lower_bound_assumption",
                        "source_url": PostTarget(post_id).url, "event_type": event_type,
                    }
    missing = set(wanted) - set(discovered)
    if missing:
        raise ResearchError("官方列表中未找到预登记帖子元数据：" + ",".join(sorted(missing)))
    result = {"discovered_at": now_iso(), "posts": [discovered[item["post_id"]] for item in study["posts"]],
              "date_assumption": "栏目首次发布时间被用作公开评论可能时间下界；这不是对底层帖子创建时间的官方保证。日期推定单独标记。"}
    write_json(cached, result)
    return result


def run(study_path: Path, *, prepare_only: bool = False) -> dict:
    study = json.loads(study_path.read_text(encoding="utf-8"))
    directory = ROOT / "data" / study["study_id"]
    directory.mkdir(parents=True, exist_ok=True)
    client = KuroClient(delay=study["delay_seconds"], timeout=25, retries=2)
    metadata = discover(client, study, directory)
    print(f"已核实 {len(metadata['posts'])} 个研究帖子。", flush=True)
    if prepare_only:
        return {"directory": str(directory), "metadata": str(directory / "posts.json")}
    failures, per_post = {}, {}
    for index, post in enumerate(metadata["posts"], 1):
        post_id = post["post_id"]
        destination = directory / "raw" / post_id
        print(f"[{index}/{len(metadata['posts'])}] {post['title']}", flush=True)
        try:
            manifest = collect_comments(client, [PostTarget(post_id)], destination,
                max_comments=study["max_comments_per_post"], max_pages=study["max_pages_per_post"],
                page_size=study["page_size"], include_replies=False, order=3,
                resume=(destination / "collection_manifest.json").exists())
            per_post[post_id] = {**manifest["posts"][post_id], "title": post["title"],
                                 "published_at": post["published_at"], "stratum": post["stratum"]}
            print(f"  保存 {manifest['total_comments']} 条，{manifest['status']}", flush=True)
        except ResearchError as exc:
            failures[post_id] = str(exc)
            print(f"  未完成：{exc}", flush=True)
            if (destination / "collection_manifest.json").exists():
                failed = json.loads((destination / "collection_manifest.json").read_text(encoding="utf-8"))
                per_post[post_id] = {**failed.get("posts", {}).get(post_id, {}), "title": post["title"], "source_url": post["source_url"],
                                     "complete_within_scope": False, "stop_reason": "failed"}
            if "限制" in str(exc) or "验证" in str(exc):
                print("访问受限，停止后续采集。", flush=True)
                break
    all_rows, seen = [], set()
    for post in metadata["posts"]:
        source = directory / "raw" / post["post_id"] / "comments.jsonl"
        if not source.exists():
            continue
        for line in source.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            key = (row["post_id"], row["comment_id"])
            if key in seen:
                continue
            seen.add(key)
            row["post_published_at"] = post["published_at"]
            all_rows.append(row)
    combined = directory / "comments.jsonl"
    temporary = combined.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in all_rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(combined)
    last_time = max((row["collected_at"] for row in all_rows), default=now_iso())
    end = (datetime.fromisoformat(last_time) + timedelta(seconds=1)).isoformat(timespec="seconds")
    manifest = {
        "dataset_kind": "live", "study_id": study["study_id"], "started_at": metadata["discovered_at"], "updated_at": now_iso(),
        "total_comments": len(all_rows), "root_comments": len(all_rows), "replies": 0,
        "status": "failed" if failures else ("complete_within_scope" if len(per_post)==len(study["posts"]) and all(p["complete_within_scope"] for p in per_post.values()) else "limited"),
        "scope": {"posts": metadata["posts"], "include_replies": False, "order": 3, "page_size": study["page_size"]},
        "limits_per_post": {"comments": study["max_comments_per_post"], "pages_per_invocation": study["max_pages_per_post"]},
        "posts": per_post, "failed_posts": failures, "sampling_note": study["sampling"],
        "period": {"start": study["start"], "end": end, "timezone": study["timezone"]},
        "privacy_note": "只存必要研究字段，作者本地HMAC匿名化；公开产物只含汇总和去身份意见概述。",
    }
    write_json(directory / "collection_manifest.json", manifest)
    print(f"合并完成：{len(all_rows)} 条，状态 {manifest['status']}。", flush=True)
    return {"directory": str(directory), "comments": len(all_rows), "start": study["start"], "end": end, "status": manifest["status"]}


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, default=ROOT / "config/month-study.json")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.study, prepare_only=args.prepare_only), ensure_ascii=False, indent=2))
    except (ResearchError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
