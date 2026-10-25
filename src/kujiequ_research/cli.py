from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import shutil
import sys

from .analysis import analyze_comments
from .cleaning import clean_comments
from .client import KuroClient
from .collector import collect_comments, write_json
from .demo import create_demo
from .models import PostTarget, ResearchError, identifier, now_iso, parse_post
from .period import filter_period
from .public_export import export_public_study
from .report import render_report


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="库街区评论采集与玩家反馈研究（角色反馈 / 优化问题）")
    commands = root.add_subparsers(dest="command", required=True)
    discover = commands.add_parser("discover", help="从公开官方公告中发现候选帖子，供人工选样")
    discover.add_argument("--game-id", type=int, choices=[2, 3], default=3)
    discover.add_argument("--pages", type=int, default=2)
    discover.add_argument("--keyword", action="append", default=[], help="标题包含任一关键词，可重复；不传则保存全部候选")
    discover.add_argument("--output", type=Path, default=Path("output/discovery"))
    discover.add_argument("--delay", type=float, default=2)

    fetch = commands.add_parser("fetch", help="按指定帖子采集评论，默认包含楼中楼")
    fetch.add_argument("--post", action="append", default=[], help="帖子 https 链接或数字 ID，可重复")
    fetch.add_argument("--posts", type=Path, help="帖子清单文件：每行一个链接，# 开头为说明")
    fetch.add_argument("--game-id", type=int, choices=[2, 3], default=3, help="纯数字ID使用的游戏：3鸣潮，2战双；完整URL优先")
    fetch.add_argument("--output", type=Path, default=Path("data/live"))
    fetch.add_argument("--max-comments", type=int, default=100, help="每帖保存评论总上限，包含回复；默认100")
    fetch.add_argument("--max-pages", type=int, default=20, help="每帖本次采集页数上限，含回复页")
    fetch.add_argument("--page-size", type=int, default=20)
    fetch.add_argument("--order", type=int, choices=[2, 3], default=3, help="2默认，3最新")
    fetch.add_argument("--delay", type=float, default=2)
    fetch.add_argument("--timeout", type=float, default=20)
    fetch.add_argument("--no-replies", action="store_true", help="只研究顶层评论，不保存内联或分页回复")
    fetch.add_argument("--resume", action="store_true", help="沿用同目录的帖子/排序/回复范围续采")

    period = commands.add_parser("period", help="按可核验时间范围筛选评论，保留日精度及推断审计")
    period.add_argument("--input", type=Path, required=True, help="归一化评论 JSONL")
    period.add_argument("--output", type=Path, required=True, help="筛选后 JSONL；与输入使用不同路径")
    period.add_argument("--start", required=True, help="含起点，建议带时区 ISO 时间，如2026-09-25T00:00:00+08:00")
    period.add_argument("--end", required=True, help="不含终点；仅日期则包含该日，建议显式带时区 ISO 时间")
    period.add_argument("--posts-metadata", type=Path, help="可选 JSON 帖子清单，含 post_id/published_at，用于唯一补年")

    clean = commands.add_parser("clean", help="保守清理、按ID去重并输出审计计数")
    clean.add_argument("--input", type=Path, required=True)
    clean.add_argument("--output", type=Path, default=Path("output/live/cleaned_comments.csv"))
    clean.add_argument("--min-length", type=int, default=2)
    clean.add_argument("--include", action="append", default=[], help="可选正文关键词OR筛选，默认保留全部")

    analyze = commands.add_parser("analyze", help="按可编辑词典进行多标签主题提及分析")
    analyze.add_argument("--input", type=Path, required=True)
    analyze.add_argument("--output", type=Path, default=Path("output/live"))
    analyze.add_argument("--config", type=Path)
    analyze.add_argument("--split-date", help="按北京时间日期比较前后，如2026-10-20；当天计入后段")

    report = commands.add_parser("report", help="从分析产物生成可离线打开的中文HTML报告")
    report.add_argument("--output", type=Path, default=Path("output/live"))

    public = commands.add_parser("export-public", help="向独立目录导出可公开的汇总数据与去身份报告")
    public.add_argument("--input-dir", type=Path, required=True, help="包含 summary.json 等分析产物的本地目录")
    public.add_argument("--output-dir", type=Path, required=True, help="独立的公开目录，禁止混入逐条评论")
    public.add_argument("--title", default="鸣潮近月玩家反馈研究", help="公开报告标题")

    pipeline = commands.add_parser("pipeline", help="对已采集JSONL一键清理、分析、生成报告")
    pipeline.add_argument("--data", type=Path, default=Path("data/live"))
    pipeline.add_argument("--output", type=Path, default=Path("output/live"))
    pipeline.add_argument("--config", type=Path)
    pipeline.add_argument("--split-date")
    pipeline.add_argument("--include", action="append", default=[])

    demo = commands.add_parser("demo", help="用明确标记的24条模拟评论离线演示完整流程")
    demo.add_argument("--output", type=Path, default=Path("output/demo"))
    return root


def copy_manifest(source_dir: Path, destination_dir: Path) -> None:
    destination_dir.mkdir(parents=True, exist_ok=True)
    source = source_dir / "collection_manifest.json"
    destination = destination_dir / "collection_manifest.json"
    if source.exists() and source.resolve() != destination.resolve():
        if destination.exists():
            old = json.loads(destination.read_text(encoding="utf-8-sig"))
            new = json.loads(source.read_text(encoding="utf-8-sig"))
            if old.get("dataset_kind") != new.get("dataset_kind"):
                raise ResearchError("真实与演示数据不能共用输出目录，请选择新目录。")
        shutil.copyfile(source, destination)
    # Validate the dataset origin above before moving any period audit. Keep the
    # audit beside analysis artifacts so export-public preserves date precision.
    period_source = source_dir / "period_summary.json"
    period_destination = destination_dir / "period_summary.json"
    if period_source.exists() and period_source.resolve() != period_destination.resolve():
        shutil.copyfile(period_source, period_destination)


def make_pipeline(raw: Path, output: Path, *, config: Path | None = None,
                  split_date: str | None = None, include: list[str] | None = None) -> dict:
    if not raw.is_file():
        raise ResearchError(f"找不到已采集数据：{raw}。请先运行 fetch，或运行 demo。")
    copy_manifest(raw.parent, output)
    cleaned = output / "cleaned_comments.csv"
    cleaning = clean_comments(raw, cleaned, include_keywords=include)
    summary = analyze_comments(cleaned, output, config_path=config, split_date=split_date)
    report = render_report(output)
    return {"cleaning": cleaning, "comments_analyzed": summary["total_comments"], "report": str(report.resolve())}


def discover_posts(args: argparse.Namespace) -> dict:
    if not 1 <= args.pages <= 20:
        raise ResearchError("官方公告发现页数应在1–20之间。")
    client = KuroClient(delay=args.delay)
    args.output.mkdir(parents=True, exist_ok=True)
    rows, seen = [], set()
    for page in range(1, args.pages + 1):
        posts = client.announcements(args.game_id, page=page, page_size=20)
        new_count = 0
        for post in posts:
            post_id = identifier(post.get("postId", ""))
            if not post_id or post_id in seen:
                continue
            seen.add(post_id)
            new_count += 1
            title = str(post.get("postTitle", post.get("title", "")))
            if args.keyword and not any(word in title for word in args.keyword):
                continue
            rows.append({"post_id": post_id, "title": title, "source_url": PostTarget(post_id, args.game_id).url,
                         "publish_time_raw": post.get("publishTime", "")})
        if not posts or not new_count:
            break
    with (args.output / "posts.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["post_id", "title", "source_url", "publish_time_raw"])
        writer.writeheader()
        for row in rows:
            row["title"] = "'" + row["title"] if row["title"].lstrip().startswith(("=", "+", "-", "@")) else row["title"]
            writer.writerow(row)
    (args.output / "posts.txt").write_text("# 官方公告候选；请查看posts.csv并人工删选后再采集。\n" + "".join(row["source_url"] + "\n" for row in rows), encoding="utf-8")
    write_json(args.output / "discovery_manifest.json", {
        "collected_at": now_iso(), "source": "official_announcements", "keywords": args.keyword,
        "candidate_posts": len(rows), "requests": client.request_count,
        "note": "这是官方公告标题筛选，未检索全社区。无结果不表示没有相关玩家讨论。",
    })
    return {"candidate_posts": len(rows), "post_list": str((args.output / "posts.txt").resolve())}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command == "fetch":
            values = list(args.post)
            if args.posts:
                values.extend(line.strip() for line in args.posts.read_text(encoding="utf-8-sig").splitlines()
                              if line.strip() and not line.lstrip().startswith("#"))
            targets = [parse_post(value, args.game_id) for value in values]
            client = KuroClient(delay=args.delay, timeout=args.timeout)
            result = collect_comments(client, targets, args.output, max_comments=args.max_comments,
                                      max_pages=args.max_pages, page_size=args.page_size,
                                      include_replies=not args.no_replies, order=args.order, resume=args.resume)
            print(f"已保存 {result['total_comments']} 条真实公开评论；状态：{result['status']}。")
            print(f"采集清单：{(args.output / 'collection_manifest.json').resolve()}")
        elif args.command == "discover":
            print(json.dumps(discover_posts(args), ensure_ascii=False, indent=2))
        elif args.command == "period":
            result = filter_period(args.input, args.output, args.start, args.end, posts_path=args.posts_metadata)
            print(f"日期筛选：输入 {result['input_rows']} 条，纳入 {result['kept_rows']} 条；范围外 {result['outside_period_rows']} 条，日期不明 {result['undetermined_date_rows']} 条，格式异常 {result['malformed_rows']} 条。")
            print(f"纳入日精度 {result['kept_day_precision_rows']} 条（其中年份推断 {result['kept_day_precision_inferred_rows']} 条），精确时间 {result['kept_exact_timestamp_rows']} 条；日精度不提供精确创建时刻。")
            print(f"筛选审计：{(args.output.parent / 'period_summary.json').resolve()}")
        elif args.command == "clean":
            copy_manifest(args.input.parent, args.output.parent)
            print(json.dumps(clean_comments(args.input, args.output, min_length=args.min_length, include_keywords=args.include), ensure_ascii=False, indent=2))
        elif args.command == "analyze":
            copy_manifest(args.input.parent, args.output)
            summary = analyze_comments(args.input, args.output, config_path=args.config, split_date=args.split_date)
            print(f"已分析 {summary['total_comments']} 条评论，输出：{args.output.resolve()}")
        elif args.command == "report":
            print(render_report(args.output).resolve())
        elif args.command == "export-public":
            result = export_public_study(args.input_dir, args.output_dir, title=args.title)
            print(f"已导出公开研究：{result['total_comments']} 条纳入评论，{len(result['sources'])} 个来源帖子，{len(result['findings'])} 项研究发现。")
            print(f"公开报告：{(args.output_dir / 'index.html').resolve()}")
        elif args.command == "pipeline":
            print(json.dumps(make_pipeline(args.data / "comments.jsonl", args.output, config=args.config,
                                           split_date=args.split_date, include=args.include), ensure_ascii=False, indent=2))
        elif args.command == "demo":
            raw = create_demo(args.output)
            print(json.dumps(make_pipeline(raw, args.output), ensure_ascii=False, indent=2))
            print("本报告全部使用模拟数据；不代表真实玩家反馈。")
        return 0
    except KeyboardInterrupt:
        print("已中断；真实采集进度已保留，可使用相同参数加 --resume 继续。", file=sys.stderr)
        return 130
    except (ResearchError, ValueError, OSError, KeyError) as exc:
        print(f"未完成：{exc}", file=sys.stderr)
        return 2

