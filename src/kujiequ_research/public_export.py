"""Publish aggregate research only, with an explicit field allowlist.

The optional recommendations.json contains researcher-written paraphrases, never
raw comments. Supported keys: review_method, reviewed_comments, findings (category,
title, observation, recommendation, validation, evidence_count, source_urls,
caveat), and source_summary (source_url, title, published_at, collected_comments,
included_comments). All other input fields are discarded.
"""

from __future__ import annotations

import csv
import html
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .report import _bar_chart, _daily_chart


PUBLIC_FILES = frozenset({"summary.json", "topics.csv", "daily_counts.csv", "sources.csv", "index.html", "README.md"})
TOPIC_FIELDS = ["id", "name", "group", "comment_count", "share_of_all"]
DAILY_FIELDS = ["date", "comment_count", "matched_comment_count", "character_feedback_count", "optimization_count"]
SOURCE_FIELDS = ["source_url", "title", "published_at", "collected_comments", "included_comments", "complete_within_scope"]
_SENSITIVE_TEXT = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:Users|home|tmp|var|root|mnt)/|(?:bearer\s+|(?:token|password|secret|private_salt|api[_-]?key)\s*[:=]))", re.I)


def _text(value: object, limit: int = 2000) -> str:
    """Accept scalar prose only; suppress apparent local paths or credentials."""
    if not isinstance(value, str):
        return ""
    value = "".join(ch for ch in value if ch in "\n\t" or ord(ch) >= 32).strip()
    return "[内容包含本地路径或凭据样式，未公开]" if _SENSITIVE_TEXT.search(value) else value[:limit]


def _count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _choice(value: object, allowed: set[str], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _day(value: object) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if _day(value):
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.isoformat() if parsed.tzinfo else None
    except ValueError:
        return None


def _source_url(value: object) -> str:
    """Only public numeric Kurobbs post permalinks; remove query/fragment."""
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.netloc in {"www.kurobbs.com", "kurobbs.com"}
            and re.fullmatch(r"/(?:[a-zA-Z0-9_-]+/)?post/\d+/?", parsed.path)
        )
        return f"https://{parsed.netloc}{parsed.path.rstrip('/')}" if valid else ""
    except ValueError:
        return ""


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value if isinstance(value, dict) else {}


def _rows(value: object) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for item in rows:
            # Prevent spreadsheet formula interpretation in every textual field.
            writer.writerow({key: "'" + val if isinstance(val := item.get(key), str) and val.lstrip().startswith(("=", "+", "-", "@")) else val for key in fields})


def _sources(manifest: dict, recommendations: dict) -> list[dict]:
    result: dict[str, dict] = {}
    scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
    posts = manifest.get("posts") if isinstance(manifest.get("posts"), dict) else {}
    inputs = _rows(scope.get("posts")) + [item for item in posts.values() if isinstance(item, dict)]
    inputs += _rows(recommendations.get("source_summary"))
    for item in inputs:
        url = _source_url(item.get("source_url"))
        if not url:
            continue
        target = result.setdefault(url, {"source_url": url, "title": "", "published_at": None, "collected_comments": 0, "included_comments": None, "complete_within_scope": None})
        if item.get("title"):
            target["title"] = _text(item["title"], 240)
        timestamp = _timestamp(item.get("published_at") or item.get("publish_time"))
        if timestamp:
            target["published_at"] = timestamp
        if "comments_saved" in item or "collected_comments" in item:
            target["collected_comments"] = _count(item.get("collected_comments", item.get("comments_saved")))
        if "included_comments" in item:
            target["included_comments"] = _count(item["included_comments"])
        if isinstance(item.get("complete_within_scope"), bool):
            target["complete_within_scope"] = item["complete_within_scope"]
    return list(result.values())


def _findings(recommendations: dict, total: int) -> list[dict]:
    findings = []
    for item in _rows(recommendations.get("findings")):
        urls = list(dict.fromkeys(url for value in item.get("source_urls", []) if (url := _source_url(value)))) if isinstance(item.get("source_urls"), list) else []
        findings.append({
            "category": _choice(item.get("category"), {"character", "optimization"}, "other"),
            **{key: _text(item.get(key)) for key in ["title", "observation", "recommendation", "validation", "caveat"]},
            "evidence_count": min(total, _count(item.get("evidence_count"))),
            "source_count": len(urls), "source_urls": urls,
        })
    return findings


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _report(study: dict) -> str:
    total = study["total_comments"]
    window = study["study_window"]
    interval = f"{window['start'] or '未设定'} 至 {window['end'] or '未设定'}"
    empty = '<div class="notice">当前为零样本，不能支持玩家反馈结论。</div>' if not total else ""
    kind = study["dataset_kind"]
    if kind != "live":
        empty += '<div class="notice">来源未确认为纯真实评论；演示、混合或未核实数据不能当作实际玩家研究结果。</div>'
    cards = []
    for item in study["findings"]:
        links = " · ".join(f'<a href="{_e(url)}" rel="noopener noreferrer">来源 {index + 1}</a>' for index, url in enumerate(item["source_urls"]))
        category = {"character": "角色", "optimization": "优化", "other": "其他"}[item["category"]]
        cards.append(f'<article class="finding"><span class="tag">{category}</span><h3>{_e(item["title"])}</h3><dl><dt>观察发现</dt><dd>{_e(item["observation"]) or "待补充核查结果"}</dd><dt>建议</dt><dd>{_e(item["recommendation"]) or "待补充"}</dd><dt>待验证</dt><dd>{_e(item["validation"]) or "待设计验证方案"}</dd></dl><p class="meta">核查证据 {item["evidence_count"]:,} 条 · 来源 {item["source_count"]} 个</p><p>{links}</p><p class="note">{_e(item["caveat"]) or "定向评论样本，只支持研究线索，不能推广至全体玩家。"}</p></article>')
    findings = "".join(cards) or '<p class="note">尚未提供经研究者核查的发现与建议；主题频次不能直接推断玩家意见。</p>'
    complete_labels = {True: "是（所设范围内）", False: "否，部分采样", None: "未记录"}
    source_rows = "".join(f'<tr><td><a href="{_e(s["source_url"])}" rel="noopener noreferrer">{_e(s["title"]) or "查看来源帖"}</a></td><td>{_e(s["published_at"]) or "未记录"}</td><td>{s["collected_comments"]:,}</td><td>{s["included_comments"] if s["included_comments"] is not None else "未记录"}</td><td>{complete_labels[s["complete_within_scope"]]}</td></tr>' for s in study["sources"])
    limitations = "".join(f"<li>{_e(item)}</li>" for item in study["limitations"])
    topic_table = "".join(f'<tr><td>{_e(t["name"])}</td><td>{t["comment_count"]:,}</td><td>{t["share_of_all"]:.1%}</td></tr>' for t in study["topic_counts"])
    method = study["methodology"]
    period = study["period_counts"]
    time_labels = {
        "kept_rows": "日期筛选保留记录", "kept_day_precision_rows": "保留的日精度记录",
        "kept_day_precision_inferred_rows": "保留的年份推断记录（日精度）",
        "day_precision_inferred_rows": "完成年份推断的记录（含后续排除）",
        "kept_exact_timestamp_rows": "保留的精确时间记录",
        "undetermined_date_rows": "日期无法判定而排除", "boundary_overlap_rows": "日期区间跨窗口而排除",
    }
    time_rows = "".join(f'<tr><th>{label}</th><td>{period[key]:,}</td></tr>' for key, label in time_labels.items() if key in period)
    precision_section = f'<table><tbody>{time_rows}</tbody></table>' if time_rows else '<p class="note">未提供日期精度与年份推断计数。</p>'
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"><title>{_e(study['title'])}</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#f3f6f8;color:#172b3a;font:16px/1.75 "Microsoft YaHei",system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:38px 26px 70px}}header{{padding:15px 0 25px}}h1{{font-size:36px;line-height:1.35;margin:10px 0}}h2{{font-size:24px;margin:0 0 18px}}h3{{font-size:20px;margin:8px 0 15px}}.eyebrow,.tag{{color:#087e8b;font-size:13px;font-weight:700}}.note,.meta{{color:#617383;font-size:14px}}.notice{{background:#fff0dc;border-left:4px solid #d9823b;padding:14px 18px;margin:18px 0}}section,.metric{{background:#fff;border:1px solid #e0e8ee;border-radius:14px;padding:25px;margin-bottom:22px}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}}.metric strong{{display:block;font-size:32px}}.metric span{{font-size:13px;color:#617383}}.finding{{border-top:1px solid #e0e8ee;padding:22px 0}}.finding:first-of-type{{border-top:0}}dt{{color:#087e8b;font-weight:700}}dd{{margin:3px 0 15px;white-space:pre-wrap}}svg{{display:block;width:100%;height:auto;font-family:"Microsoft YaHei",system-ui,sans-serif}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{text-align:left;padding:12px 10px;border-bottom:1px solid #e0e8ee;vertical-align:top}}th{{background:#f4f8fa}}a{{color:#087e8b;overflow-wrap:anywhere}}.scroll{{overflow-x:auto}}.empty{{color:#617383;padding:25px}}li{{margin:8px 0}}footer{{font-size:12px;color:#617383}}@media(max-width:650px){{main{{padding:20px 15px}}h1{{font-size:28px}}section{{padding:20px}}.metrics{{grid-template-columns:1fr;gap:0}}.metric{{padding:17px}}}}</style></head>
<body><main><header><div class="eyebrow">库街区 · 公开研究作品</div><h1>{_e(study['title'])}</h1><p>角色反馈与优化问题｜定向公开评论研究</p><p class="note">研究窗口：{_e(interval)}（UTC+08:00；含起点、不含终点）<br>实际样本日期：{_e(study['date_range']['start']) or '无'} 至 {_e(study['date_range']['end']) or '无'}<br>窗口时刻是筛选参数，不是评论精确创建时刻。</p></header>{empty}
<div class="metrics"><div class="metric"><span>纳入分析评论</span><strong>{total:,}</strong></div><div class="metric"><span>主题规则命中</span><strong>{study['matched_comments']:,}</strong></div><div class="metric"><span>来源帖子</span><strong>{len(study['sources'])}</strong></div></div>
<section><div class="eyebrow">研究结论</div><h2>角色与优化：发现、建议和验证</h2><p class="note">{_e(method['review_method'])}；核查 {study['reviewed_comments']:,} 条。以下是去身份概述，不展示逐条评论原文；不将提及量直接解释为负面意见。</p>{findings}</section>
<section><div class="eyebrow">主题分布</div><h2>样本中出现了哪些话题</h2><p class="note">多标签统计；分母为全部 {total:,} 条纳入评论，合计可超过 100%。主题命中同时包含肯定、否定和转述。</p>{_bar_chart(study['topic_counts'], total)}<details><summary>查看汇总表</summary><table><thead><tr><th>主题</th><th>提及条数</th><th>占全部评论</th></tr></thead><tbody>{topic_table}</tbody></table></details></section>
<section><div class="eyebrow">时间分布</div><h2>纳入样本的评论日期</h2><p class="note">缺失或无效日期 {study['unknown_time_comments']:,} 条。日评论量受帖子选择和采集上限影响，不能视为玩家活跃度或总体舆情趋势。</p>{_daily_chart(study['daily'])}<h3>日期精度与推断审计</h3><p>{_e(method['date_precision'])}</p>{precision_section}<p class="note">以上为日期筛选阶段、文本清洗前的记录计数，可能与最终纳入分析条数不同。</p><p class="note">{_e(method['date_inference_assumption'])}</p></section>
<section><div class="eyebrow">可核查来源</div><h2>公开帖子与样本汇总</h2><p class="note">保存数为清洗前口径，纳入数为清洗及日期筛选后口径；未记录值表示未提供，不作零值解释。</p><div class="scroll"><table><thead><tr><th>帖子</th><th>发布日期</th><th>采集条数</th><th>纳入条数</th><th>完成所设范围</th></tr></thead><tbody>{source_rows}</tbody></table></div></section>
<section><div class="eyebrow">方法与局限</div><h2>结论能说明什么</h2><p>{_e(method['sampling'])}</p><p>{_e(method['labeling'])}</p><ul>{limitations}</ul><p class="note">公开文件仅含汇总数据、帖子来源及去身份研究概述；逐条评论、作者标识与本地采集记录不进入公开目录。</p></section>
<footer>生成时间：{_e(study['generated_at'])} · 自包含报告，无远程脚本、字体和跟踪资源。<br>配套文件：summary.json、topics.csv、daily_counts.csv、sources.csv、README.md。</footer></main></body></html>'''


def export_public_study(input_dir: Path, output_dir: Path, title: str = "鸣潮近月玩家反馈研究") -> dict:
    """Write six allowlisted files to a separate, uncontaminated public folder.

    Does not copy arbitrary metadata or read row-level comment files. Existing
    unknown files are rejected rather than silently leaving private data beside
    the report. Invalid JSON raises instead of producing a misleading report.
    """
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    if input_dir.resolve() == output_dir.resolve():
        raise ValueError("公开目录必须与含逐条评论的分析目录分开")
    if output_dir.is_symlink() or (output_dir.exists() and any(p.name not in PUBLIC_FILES or not p.is_file() or p.is_symlink() for p in output_dir.iterdir())):
        raise ValueError("公开目录含未许可文件、目录或符号链接；请使用独立的干净目录")
    source = _read_json(input_dir / "summary.json")
    if not source:
        raise ValueError("缺少有效 summary.json，无法导出研究")
    manifest = _read_json(input_dir / "collection_manifest.json")
    cleaning = _read_json(input_dir / "cleaning_summary.json")
    period = _read_json(input_dir / "period_summary.json")
    recommendations = _read_json(input_dir / "recommendations.json")
    total = _count(source.get("total_comments"))
    topics = []
    for item in _rows(source.get("topic_counts")) or _read_csv(input_dir / "topics.csv"):
        count = min(total, _count(item.get("comment_count")))
        topics.append({"id": _text(item.get("id"), 80), "name": _text(item.get("name"), 80), "group": _choice(item.get("group"), {"character", "optimization"}, "other"), "comment_count": count, "share_of_all": round(count / total, 6) if total else 0.0})
    daily = []
    for item in _rows(source.get("daily")) or _read_csv(input_dir / "daily_counts.csv"):
        if day := _day(item.get("date")):
            daily.append({"date": day, **{key: min(total, _count(item.get(key))) for key in DAILY_FIELDS[1:]}})
    daily.sort(key=lambda item: item["date"])
    date_range = source.get("date_range") if isinstance(source.get("date_range"), dict) else {}
    window = period.get("window") if isinstance(period.get("window"), dict) else {}
    scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
    period_window = {"start": _timestamp(period.get("start") or period.get("start_date") or window.get("start")), "end": _timestamp(period.get("end") or period.get("end_date") or window.get("end"))}
    sources = _sources(manifest, recommendations)
    limits = [
        "这是选定帖子内的定向便利样本，不是随机抽样；不能代表全部库街区用户或鸣潮玩家，评论条数不等于玩家人数。",
        "采集排序、分页与条数上限会影响样本构成；完成所设范围也不等于采完整个平台或近一个月全部评论。",
        "主题词典只识别提及，不判断正负态度、问题严重程度或因果；存在误判与漏判。",
        "研究者核查用于解释具体样本，未经独立双人标注和一致性检验，不能报告分类准确率或总体负面率。",
        "建议是待验证的产品假设，应结合设备、场景、角色熟练度访谈及性能或可用性测试验证。",
        "日期推断以官方栏目发布时间为公开评论下界的假设，不是底层创建时间官方保证；月日显示仅在发布时间与采集时间约束下存在唯一可能年份时推断，日精度不扩充为精确创建时刻。",
    ]
    kinds = {_choice(item.get("dataset_kind"), {"live", "demo", "mixed", "unverified"}, "") for item in [source, cleaning, manifest]} - {""}
    kind = "mixed" if "mixed" in kinds or {"live", "demo"} <= kinds else _choice(source.get("dataset_kind"), {"live", "demo", "unverified"}, "unverified")
    all_day = _count(period.get("kept_rows")) > 0 and _count(period.get("kept_day_precision_rows")) == _count(period.get("kept_rows")) and _count(period.get("kept_exact_timestamp_rows")) == 0
    precision_note = ("本次日期筛选保留的记录全部为日精度，没有精确评论创建时刻。" if all_day else "日精度记录只确定日历日期，不提供评论精确创建时刻。") + "根据月日显示推断年份的记录单独计数。"
    study = {
        "schema_version": 1, "title": _text(title, 160), "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "dataset_kind": kind,
        "timezone": "UTC+08:00", "study_window": period_window,
        "date_range": {"start": _day(date_range.get("start")), "end": _day(date_range.get("end"))},
        "total_comments": total, "matched_comments": min(total, _count(source.get("matched_comments"))),
        "multi_topic_comments": min(total, _count(source.get("multi_topic_comments"))),
        "unknown_time_comments": min(total, _count(source.get("unknown_time_comments"))),
        "topic_counts": topics, "daily": daily, "sources": sources,
        "reviewed_comments": min(total, _count(recommendations.get("reviewed_comments"))),
        "findings": _findings(recommendations, total),
        "methodology": {"sampling": "选定公开帖子内按指定排序、分页及上限采集；按评论日期的可能区间筛选研究窗口，仅保留整个可能区间均在窗口内的记录。", "labeling": "关键词词典、多标签主题提及统计；每项占比以全部纳入评论数为分母。", "review_method": _text(recommendations.get("review_method")) or "尚未提供研究者核查记录", "include_replies": scope.get("include_replies") if isinstance(scope.get("include_replies"), bool) else None, "order": _count(scope.get("order")) or None,
            "date_precision": precision_note,
            "date_inference_assumption": "日期推断以官方栏目发布时间为公开评论下界的假设，不是底层创建时间官方保证；采集时间为可见评论的上界。只有满足已知边界且存在唯一可能年份的月日显示才能推断。"},
        "cleaning_counts": {key: _count(cleaning[key]) for key in ["input_rows", "kept_rows", "duplicate_rows", "empty_rows", "too_short_rows", "malformed_rows", "invalid_created_at_rows"] if key in cleaning},
        "period_counts": {key: _count(period[key]) for key in ["input_rows", "kept_rows", "included_rows", "outside_period_rows", "before_window_rows", "after_window_rows", "invalid_time_rows", "undetermined_date_rows", "day_precision_inferred_rows", "kept_day_precision_inferred_rows", "kept_day_precision_rows", "kept_exact_timestamp_rows", "boundary_overlap_rows", "malformed_rows"] if key in period},
        "limitations": limits,
    }
    if kind != "live":
        study["limitations"].insert(0, "数据来源未确认为纯真实评论，当前内容不能作为实际玩家研究结论。")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(study, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_csv(output_dir / "topics.csv", TOPIC_FIELDS, topics)
    _write_csv(output_dir / "daily_counts.csv", DAILY_FIELDS, daily)
    _write_csv(output_dir / "sources.csv", SOURCE_FIELDS, sources)
    (output_dir / "index.html").write_text(_report(study), encoding="utf-8")
    start, end = period_window["start"] or "未设定", period_window["end"] or "未设定"
    lines = [f"# {study['title']}", "", f"研究窗口：{start} 至 {end}（UTC+08:00；含起点、不含终点）。实际纳入 {total:,} 条评论，来源 {len(sources)} 个帖子。", "", "窗口时刻是筛选参数，不是评论精确创建时刻。", "", "本目录仅公开汇总数据与去身份研究概述，未包含逐条评论及作者标识。", "", "- `index.html`：用浏览器打开自包含研究报告。", "- `summary.json`：计数、方法、局限、来源和经核查的建议。", "- `topics.csv`：主题提及条数与占全部纳入评论的比例。", "- `daily_counts.csv`：纳入样本日期分布；不是玩家活跃度。", "- `sources.csv`：公开帖子链接与逐帖汇总；空值表示未记录。", "", "## 日期精度与推断", "", study["methodology"]["date_precision"], "", study["methodology"]["date_inference_assumption"], "", "下列为日期筛选阶段、文本清洗前的记录计数：", ""]
    for key, label in [("kept_day_precision_rows", "保留的日精度记录"), ("kept_day_precision_inferred_rows", "保留的年份推断记录（日精度）"), ("day_precision_inferred_rows", "完成年份推断的记录（含后续排除）"), ("kept_exact_timestamp_rows", "保留的精确时间记录"), ("undetermined_date_rows", "日期无法判定而排除")]:
        if key in study["period_counts"]:
            lines.append(f"- {label}：{study['period_counts'][key]:,} 条。")
    lines += ["", "## 研究发现与建议", ""]
    if not study["findings"]:
        lines += ["尚未提供经研究者核查的发现与建议。主题命中量不能直接解释为负面意见。", ""]
    for finding in study["findings"]:
        lines += [f"### {finding['title']}", "", f"观察发现：{finding['observation']}", "", f"建议：{finding['recommendation']}", "", f"待验证：{finding['validation']}", "", f"核查证据：{finding['evidence_count']} 条；来源：{finding['source_count']} 个。", "", f"局限：{finding['caveat']}", ""]
        lines += [f"- [来源 {index + 1}]({url})" for index, url in enumerate(finding["source_urls"])]
        lines += [""]
    lines += ["## 方法与边界", "", study["methodology"]["review_method"], ""] + [f"- {item}" for item in limits]
    (output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return study
