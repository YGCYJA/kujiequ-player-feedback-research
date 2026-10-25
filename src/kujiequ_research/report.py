"""Self-contained, escaped HTML report; no remote scripts or assets."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from urllib.parse import urlparse


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _number(value: object) -> int:
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        return 0


def _source_link(value: object, label: str = "查看来源") -> str:
    """Allow only escaped HTTP(S) links, even for hand-edited manifests."""
    source = str(value or "")
    try:
        parsed = urlparse(source)
        allowed = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except ValueError:
        allowed = False
    if not allowed:
        return "无有效来源链接"
    return f'<a href="{_e(source)}" target="_blank" rel="noopener noreferrer">{_e(label)}</a>'


def _status_label(value: object) -> str:
    return {
        "initialized": "等待采集", "running": "采集中",
        "complete_within_scope": "已完成所设采集范围",
        "limited": "达到采样上限，未采完整",
        "interrupted": "采集中断，未采完整", "failed": "采集失败，未采完整",
    }.get(str(value), "状态未记录或未知")


def _collection_scope(manifest: dict) -> str:
    """Readable allowlist of sampling settings and per-post completion evidence."""
    if not manifest:
        return '<section class="card"><div class="eyebrow">采集范围</div><h2>采集记录待补充</h2><p class="note">当前目录未提供采集记录，无法确认选帖、排序、回复范围及采样上限。</p></section>'
    scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
    limits = manifest.get("limits_per_post") if isinstance(manifest.get("limits_per_post"), dict) else {}
    posts = manifest.get("posts") if isinstance(manifest.get("posts"), dict) else {}
    selected = scope.get("posts") if isinstance(scope.get("posts"), list) else []
    # Preserve selected but not yet collected posts, e.g. after an interruption.
    rows = {str(key): value for key, value in posts.items() if isinstance(value, dict)}
    for post in selected:
        if isinstance(post, dict) and post.get("post_id") is not None:
            ident = str(post["post_id"])
            if ident not in rows:
                rows[ident] = {"source_url": post.get("source_url", "")}

    def setting(value: object, suffix: str = "") -> str:
        if isinstance(value, bool):
            return "未记录"
        number = _number(value)
        return f"{number:,}{suffix}" if number else "未记录"

    order = {"2": "默认排序（2）", "3": "最新排序（3）"}.get(str(scope.get("order")), "未记录或未知排序")
    replies = "包含楼中楼回复" if scope.get("include_replies") is True else "仅主评论" if scope.get("include_replies") is False else "未记录"
    settings = [
        ("采集状态", _status_label(manifest.get("status"))),
        ("帖子数量", f"{len(rows):,} 个"),
        ("评论排序", order), ("回复范围", replies),
        ("每页条数设置", setting(scope.get("page_size"), " 条")),
        ("每帖评论上限", setting(limits.get("comments"), " 条")),
        ("每帖本次页数上限", setting(limits.get("pages_per_invocation"), " 页")),
    ]
    settings_rows = "".join(f'<tr><th>{_e(label)}</th><td>{_e(value)}</td></tr>' for label, value in settings)
    reasons = {
        "page_limit": "达到本次页数上限", "comment_limit": "达到评论条数上限",
        "complete_within_scope": "已遍历所设范围", "not_finished": "尚未采集完成",
    }
    post_rows = []
    for ident, post in rows.items():
        complete = "是（所设范围内）" if post.get("complete_within_scope") is True else "否，未采完整" if post.get("complete_within_scope") is False else "未记录"
        count = f'{_number(post["comments_saved"]):,}' if "comments_saved" in post else "未记录"
        reason = reasons.get(str(post.get("stop_reason")), "未记录或未知原因")
        post_rows.append(f'<tr><td>{_e(ident)}<br>{_source_link(post.get("source_url"), "查看帖子")}</td><td>{count}</td><td>{complete}</td><td>{_e(reason)}</td></tr>')
    posts_table = '<div class="table-wrap"><table><thead><tr><th>帖子与来源</th><th>已保存评论（清洗前）</th><th>是否采完整</th><th>停止原因</th></tr></thead><tbody>' + "".join(post_rows) + '</tbody></table></div>' if post_rows else '<p class="note">未记录逐帖采集结果。</p>'
    return '<section class="card"><div class="eyebrow">采集范围</div><h2>这份样本如何获得</h2><p class="note">以下为实际采集设置与逐帖记录。达到上限时只获得部分评论，不是帖子全量，也不是随机抽样；即使完成所设范围，也不能代表全部玩家。</p><table class="scope-table"><tbody>' + settings_rows + '</tbody></table>' + posts_table + '<p class="note">保存条数是清洗前口径，可能与分析纳入条数不同；每帖本次页数上限包含请求主评论及楼中楼所用的分页。</p></section>'


def _bar_chart(topics: list[dict], total: int) -> str:
    if not topics or not total:
        return '<div class="empty">暂无可绘制的数据。完成采集并分析后，这里会显示主题分布。</div>'
    rows = sorted(topics, key=lambda item: item.get("comment_count", 0), reverse=True)
    width, height = 900, 40 + 44 * len(rows)
    bars = []
    for index, item in enumerate(rows):
        y = 30 + 44 * index
        count = _number(item.get("comment_count"))
        proportion = count / total
        length = min(560, 560 * proportion)
        color = "#087e8b" if item.get("group") == "character" else "#d9823b"
        bars.append(f'<text x="0" y="{y + 18}" fill="#344454" font-size="16">{_e(item.get("name"))}</text><rect x="165" y="{y}" width="560" height="25" rx="5" fill="#eff3f6"/><rect x="165" y="{y}" width="{length:.2f}" height="25" rx="5" fill="{color}"/><text x="745" y="{y + 18}" fill="#344454" font-size="15">{count:,} · {proportion:.1%}</text>')
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="各主题提及条数及占全部评论的比例">' + "".join(bars) + '</svg>'


def _daily_chart(daily: list[dict]) -> str:
    if not daily:
        return '<div class="empty">没有有效日期，暂不显示每日评论量。</div>'
    maximum = max(_number(day.get("comment_count")) for day in daily) or 1
    width, height = 900, 240
    left, top, plot_width, plot_height = 55, 20, 790, 160
    # Aggregate rendering to a bounded number of points for very long studies.
    stride = max(1, (len(daily) + 119) // 120)
    buckets = []
    for start in range(0, len(daily), stride):
        chunk = daily[start:start + stride]
        buckets.append({"date": chunk[0]["date"], "last_date": chunk[-1]["date"], "count": sum(_number(item.get("comment_count")) for item in chunk)})
    maximum = max(item["count"] for item in buckets) or 1
    gap = plot_width / len(buckets)
    bars = [f'<path d="M {left} {top} V {top + plot_height} H {left + plot_width}" fill="none" stroke="#c9d4de"/>']
    for index, bucket in enumerate(buckets):
        bar_height = plot_height * bucket["count"] / maximum
        x = left + index * gap + gap * .15
        bars.append(f'<rect x="{x:.2f}" y="{top + plot_height - bar_height:.2f}" width="{max(gap * .7, 1):.2f}" height="{bar_height:.2f}" rx="2" fill="#087e8b"><title>{_e(bucket["date"])} 至 {_e(bucket["last_date"])}：{bucket["count"]} 条</title></rect>')
    bars.append(f'<text x="{left - 12}" y="{top + 5}" text-anchor="end" fill="#6a7885" font-size="13">{maximum}</text>')
    bars.append(f'<text x="{left - 12}" y="{top + plot_height}" text-anchor="end" fill="#6a7885" font-size="13">0</text>')
    for x, day, anchor in [(left, daily[0]["date"], "start"), (left + plot_width, daily[-1]["date"], "end")]:
        bars.append(f'<text x="{x}" y="210" text-anchor="{anchor}" fill="#6a7885" font-size="14">{_e(day)}</text>')
    note = '超过 120 个有评论日期时，按相邻日期分桶求和。' if stride > 1 else '仅显示样本中有评论的日期；空白日期未补零。'
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="采样评论的时间分布">' + "".join(bars) + f'</svg><p class="note">{note} 日评论量不是用户活跃度。</p>'


def _optional_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def render_report(output_dir: Path) -> Path:
    """Render summary.json and review_sample.csv into a portable HTML file."""
    output_dir = Path(output_dir)
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8-sig"))
    total = _number(summary.get("total_comments"))
    matched = _number(summary.get("matched_comments"))
    multi = _number(summary.get("multi_topic_comments"))
    kind = summary.get("dataset_kind", "unverified")
    kind_label = {"demo": "合成演示数据", "live": "公开评论样本", "mixed": "来源混合或冲突", "unverified": "来源待核实"}.get(kind, "来源待核实")
    banner = {
        "demo": "本报告使用合成演示评论，只用于演示采集后的分析流程，不构成实际玩家研究结论。",
        "live": "本报告描述选定公开评论样本中的主题提及。规则尚待人工核验，不能代表全体玩家意见。",
        "mixed": "检测到混合或冲突的数据来源标记，可能包含演示内容。请分离数据并核实来源后重新分析，不能将本报告作为真实玩家研究结论。",
    }.get(kind, "数据来源类型尚未标注，请先核实采集记录。当前统计不能直接作为玩家研究结论。")
    date_range = summary.get("date_range", {})
    date_text = f'{date_range.get("start")} — {date_range.get("end")}' if date_range.get("start") else "暂无有效日期"
    topics = summary.get("topic_counts", [])
    topic_chart = _bar_chart(topics, total)
    daily_chart = _daily_chart(summary.get("daily", []))
    review = []
    if (output_dir / "review_sample.csv").exists():
        with (output_dir / "review_sample.csv").open("r", encoding="utf-8-sig", newline="") as stream:
            review = list(csv.DictReader(stream))
    preview = []
    for item in review[:12]:
        source = item.get("source_url", "")
        # Never execute arbitrary schemes from imported comments.
        link = _source_link(source)
        text = item.get("text", "")
        text = text[:1200] + "……" if len(text) > 1200 else text
        preview.append(f'<tr><td class="comment">{_e(text)}</td><td>{_e(item.get("rule_topics") or "未命中")}</td><td>{_e(item.get("matched_keywords") or "—")}</td><td>{link}<span class="muted">待人工核验</span></td></tr>')
    review_rows = "".join(preview) or '<tr><td colspan="4" class="empty">当前没有可核验的评论。</td></tr>'
    topic_table_rows = "".join(f'<tr><td>{_e(item.get("name"))}</td><td>{_number(item.get("comment_count")):,}</td><td>{float(item.get("share_of_all", 0)):.1%}</td></tr>' for item in sorted(topics, key=lambda item: item.get("comment_count", 0), reverse=True))
    group_cards = "".join(f'<div class="group"><span>{_e(item.get("name"))}</span><strong>{_number(item.get("comment_count")):,}<small> 条 · {float(item.get("share_of_all", 0)):.1%}</small></strong><p>同组内去重后的主题提及</p></div>' for item in summary.get("groups", []))
    limitations = "".join(f'<li>{_e(item)}</li>' for item in summary.get("limitations", []))
    split = summary.get("split_comparison")
    split_section = ""
    if split:
        before, after = split["before"], split["after"]
        left = {item["id"]: item for item in before["topics"]}
        right = {item["id"]: item for item in after["topics"]}
        split_rows = []
        for item in topics:
            a, b = left.get(item["id"], {}), right.get(item["id"], {})
            a_share, b_share = float(a.get("share", 0)), float(b.get("share", 0))
            a_label = f'{a_share:.1%}' if before["total_comments"] else "无样本"
            b_label = f'{b_share:.1%}' if after["total_comments"] else "无样本"
            diff = f'{(b_share - a_share) * 100:+.1f} 个百分点' if before["total_comments"] and after["total_comments"] else "不可比较"
            split_rows.append(f'<tr><td>{_e(item["name"])}</td><td>{a_label}</td><td>{b_label}</td><td>{diff}</td></tr>')
        split_section = f'<section class="card"><div class="eyebrow">时间对比</div><h2>分界日：{_e(split["split_date"])}</h2><p>前段 {before["total_comments"]:,} 条，后段 {after["total_comments"]:,} 条。{_e(split["definition"])}。</p><p class="note">仅比较两个时间段的采样讨论分布，不能解释为改版或角色调整造成的效果。</p><div class="table-wrap"><table><thead><tr><th>主题</th><th>前段占比</th><th>后段占比</th><th>比例差</th></tr></thead><tbody>{"".join(split_rows)}</tbody></table></div></section>'
    manifest = _optional_json(output_dir / "collection_manifest.json")
    cleaning = _optional_json(output_dir / "cleaning_summary.json")
    scope_section = _collection_scope(manifest)
    valid_time_count = sum(_number(day.get("comment_count")) for day in summary.get("daily", []))
    unknown_time_count = _number(summary.get("unknown_time_comments"))
    date_coverage = f"日期覆盖：{valid_time_count:,} / {total:,} 条（{valid_time_count / total:.1%}）。" if total else "日期覆盖：无样本。"
    # Avoid dumping arbitrary manifest fields, request headers or session credentials.
    safe_keys = {"dataset_kind", "generated_at", "started_at", "finished_at", "collected_at", "status", "post_count", "comment_count", "total_comments", "raw_count", "input_rows", "output_rows", "kept_rows", "removed_duplicates", "removed_empty", "duplicate_rows", "empty_rows", "too_short_rows", "keyword_excluded_rows", "min_length", "include_keywords", "deduplication_policy", "formula_escaped_rows", "invalid_created_at_rows", "input_count", "output_count", "max_pages", "max_comments", "include_replies", "endpoint", "source", "errors", "timezone"}
    meta_sections = []
    for label, metadata in [("采集记录", manifest), ("清洗记录", cleaning)]:
        safe = {key: value for key, value in metadata.items() if key in safe_keys}
        if "status" in safe:
            safe["status"] = _status_label(safe["status"])
        if safe:
            meta_sections.append(f'<details><summary>{label}</summary><pre>{_e(json.dumps(safe, ensure_ascii=False, indent=2))}</pre></details>')
    config = summary.get("methodology", {}).get("config", {})
    config_rows = "".join(f'<tr><td>{_e(topic.get("name"))}</td><td>{_e("、".join(topic.get("keywords", [])))}</td></tr>' for topic in config.get("topics", []))
    empty_notice = '<div class="notice">当前为零样本报告。请先采集或导入评论，再讨论玩家反馈；所有零值均表示没有数据。</div>' if not total else ""
    report = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"><title>库街区 · 玩家反馈研究</title>
<style>
:root{{--ink:#172b3a;--muted:#617383;--teal:#087e8b;--orange:#d9823b;--line:#e1e9ee;--paper:#f3f6f8}}*{{box-sizing:border-box}}body{{margin:0;color:var(--ink);background:var(--paper);font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;line-height:1.7}}.wrap{{max-width:1120px;margin:auto;padding:36px 28px 60px}}header{{padding:35px 0 25px}}.eyebrow{{font-size:12px;color:var(--teal);font-weight:700;letter-spacing:2px}}h1{{font-size:36px;letter-spacing:-1px;line-height:1.3;margin:12px 0}}h2{{font-size:22px;margin:5px 0 14px;line-height:1.5}}p{{margin:8px 0 15px}}.sub,.note{{color:var(--muted);font-size:14px}}.badge{{display:inline-block;border:1px solid #bfd7d9;color:#12626a;background:#eaf5f4;padding:3px 11px;border-radius:20px;font-size:12px;margin-bottom:12px}}.notice{{border-left:4px solid var(--orange);background:#fff3e7;padding:14px 18px;border-radius:4px;margin:16px 0 24px;font-size:14px}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin:24px 0}}.metric,.card{{background:#fff;border:1px solid var(--line);border-radius:14px}}.metric{{padding:22px}}.metric span{{display:block;font-size:13px;color:var(--muted)}}.metric strong{{display:block;font-size:33px;line-height:1.6}}.metric small{{font-size:12px;color:var(--muted)}}.card{{padding:28px;margin-bottom:22px;box-shadow:0 3px 12px #132b3d04}}.groups{{display:flex;gap:16px;margin:18px 0}}.group{{flex:1;background:#f3f8f9;border-radius:9px;padding:16px 20px}}.group span{{display:block;color:var(--muted);font-size:14px}}.group strong{{font-size:25px}}.group small{{font-size:14px;color:var(--muted);font-weight:400}}.group p{{font-size:12px;color:var(--muted);margin:0}}.legend{{display:flex;gap:20px;font-size:13px;color:var(--muted)}}.dot{{width:9px;height:9px;display:inline-block;border-radius:50%;margin-right:7px}}svg{{display:block;width:100%;height:auto;margin-top:10px;font-family:"Microsoft YaHei","PingFang SC",sans-serif}}.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px;margin:15px 0}}th,td{{text-align:left;vertical-align:top;padding:13px 12px;border-bottom:1px solid var(--line)}}th{{background:#f5f8fa;font-weight:600;white-space:nowrap}}.comment{{min-width:260px;width:48%;white-space:pre-wrap;overflow-wrap:anywhere}}td:nth-child(2){{min-width:110px}}a{{color:var(--teal)}}.muted{{display:block;color:var(--muted);font-size:12px;margin-top:8px}}ul,ol{{padding-left:24px}}li{{margin:8px 0}}details{{margin:15px 0}}summary{{cursor:pointer;color:var(--teal);font-size:14px}}pre{{overflow:auto;background:#f5f8fa;padding:18px;font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere}}.empty{{padding:28px;text-align:center;color:var(--muted);background:#f7f9fa;border-radius:8px}}footer{{font-size:12px;color:var(--muted);padding:10px 0}}@media(max-width:700px){{.wrap{{padding:18px 15px 35px}}h1{{font-size:28px}}.metrics{{grid-template-columns:repeat(2,1fr);gap:10px}}.metric{{padding:15px}}.metric strong{{font-size:28px}}.card{{padding:19px}}.groups{{flex-direction:column}}}}
</style></head><body><main class="wrap">
<header><div class="eyebrow">库街区 / 玩家研究工作台</div><h1>角色反馈与优化讨论</h1><p class="sub">从公开评论整理研究线索，再用人工阅读与用户研究验证。</p><span class="badge">{_e(kind_label)} · 规则主题分析</span><p class="sub">有效日期样本范围：{_e(date_text)}<br>统计时区：{_e(summary.get("timezone"))}</p></header>
<div class="notice">{_e(banner)}</div>{empty_notice}
<section class="metrics" aria-label="样本概况"><div class="metric"><span>纳入评论</span><strong>{total:,}</strong><small>分析文件中的评论条数</small></div><div class="metric"><span>主题规则命中</span><strong>{matched:,}</strong><small>至少命中一个主题</small></div><div class="metric"><span>多主题评论</span><strong>{multi:,}</strong><small>同一条评论可重复计入主题</small></div><div class="metric"><span>待核验样本</span><strong>{len(review):,}</strong><small>人工核验尚未完成</small></div></section>
{scope_section}
<section class="card"><div class="eyebrow">主题分布</div><h2>玩家在讨论哪些话题</h2><p class="note">这是主题提及数量，不是负面反馈数量。每项占比 = 该主题命中评论数 ÷ 全部 {total:,} 条评论；多标签允许重叠。</p><div class="groups">{group_cards}</div><div class="legend"><span><i class="dot" style="background:#087e8b"></i>角色反馈</span><span><i class="dot" style="background:#d9823b"></i>优化相关</span></div>{topic_chart}<details><summary>查看完整数据表</summary><table><thead><tr><th>主题</th><th>提及条数</th><th>占全部评论</th></tr></thead><tbody>{topic_table_rows}</tbody></table></details></section>
<section class="card"><div class="eyebrow">采样时间</div><h2>讨论样本的时间分布</h2><p class="note">{date_coverage}图表仅覆盖有效日期样本；{unknown_time_count:,} 条缺失或无效日期未进入图表，不能将图中的趋势推广至全部纳入评论。</p>{daily_chart}<p class="note">无时区时间按 UTC+08:00 解释。日期缺失不会从主题分析中删除。</p></section>
{split_section}
<section class="card"><div class="eyebrow">证据核验</div><h2>先阅读原文，再判断问题</h2><p class="note">预览前 {min(12, len(review))} 条覆盖样本。完整待核验记录位于 review_sample.csv；样本用于检查词典误判与遗漏，不是随机抽样，也没有验证准确率。</p><div class="table-wrap"><table><thead><tr><th>评论原文</th><th>规则主题</th><th>命中词</th><th>来源与状态</th></tr></thead><tbody>{review_rows}</tbody></table></div></section>
<section class="card"><div class="eyebrow">下一步研究</div><h2>把讨论线索转为可验证的问题</h2><ol><li>人工核验原文是否表达具体问题，区分肯定、否定、转述和玩笑；在核验表中记录判断依据。</li><li>补充角色、设备、版本与触发场景，寻找同类反馈中的共同条件，更新词典后重新分析。</li><li>通过玩家访谈或可用性测试核实原因，为建议附上证据、适用范围及后续验证方案。</li></ol><p class="note">主题频次只能帮助寻找研究线索。优先级还需要结合问题严重程度、影响范围和修复成本判断。</p></section>
<section class="card"><div class="eyebrow">方法与边界</div><h2>让结论可以被检查</h2><ul>{limitations}</ul><details><summary>查看本次主题词典</summary><p class="note">修改 config/topics.json 后重新分析；英文关键词不区分大小写。</p><table><thead><tr><th>主题</th><th>关键词</th></tr></thead><tbody>{config_rows}</tbody></table></details>{"".join(meta_sections)}<p class="note">可检查文件：summary.json、topics.csv、daily_counts.csv、labeled_comments.csv、review_sample.csv、key_findings.md。</p></section>
<footer>生成时间：{_e(summary.get("generated_at"))}（ISO 8601，含 UTC 偏移） · 自包含报告，无外部脚本与字体。<br>本页面不提供情绪、满意度或分类准确率评分。</footer>
</main></body></html>'''
    path = output_dir / "report.html"
    path.write_text(report, encoding="utf-8")
    return path
