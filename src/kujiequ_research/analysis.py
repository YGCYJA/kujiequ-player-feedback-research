"""Transparent, editable topic coding for public player comments.

The rules identify *mentions*, not dissatisfaction, sentiment or verified needs.
No external model, network request or third-party package is required.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


LOCAL_TZ = timezone(timedelta(hours=8))
TIMEZONE_LABEL = "UTC+08:00（中国标准时间）"

DEFAULT_CONFIG = {
    "version": 1,
    "description": "可编辑的主题提及词典；关键词命中不等于负面评价、真实需求或模型判断。各主题允许重叠。",
    "groups": {"character": "角色反馈", "optimization": "优化相关"},
    "topics": [
        {"id": "character_mechanics", "name": "角色机制", "group": "character", "keywords": ["角色机制", "技能", "连招", "循环", "共鸣解放", "共鸣技能", "变奏", "延奏", "协奏", "共鸣回路", "大招"]},
        {"id": "character_feel", "name": "操作与手感", "group": "character", "keywords": ["手感", "打击感", "操作", "锁敌", "索敌", "后摇", "前摇", "取消动作", "闪避", "弹反"]},
        {"id": "character_progression", "name": "养成与资源", "group": "character", "keywords": ["养成", "声骸", "武器", "突破", "素材", "共鸣链", "加点", "词条", "培养成本"]},
        {"id": "character_balance", "name": "强度与数值", "group": "character", "keywords": ["强度", "伤害", "倍率", "数值", "削弱", "加强", "平衡", "输出", "超模", "膨胀"]},
        {"id": "character_presentation", "name": "角色表现", "group": "character", "keywords": ["立绘", "建模", "配音", "人设", "角色剧情", "角色设计", "动作", "表情", "服装"]},
        {"id": "performance_fps", "name": "帧率与卡顿", "group": "optimization", "keywords": ["帧率", "帧数", "掉帧", "卡顿", "卡帧", "fps", "帧生成", "稳帧", "流畅"]},
        {"id": "thermal_power", "name": "发热与耗电", "group": "optimization", "keywords": ["发热", "过热", "烫手", "温度", "耗电", "续航", "功耗", "降频"]},
        {"id": "stability", "name": "闪退与稳定性", "group": "optimization", "keywords": ["闪退", "崩溃", "黑屏", "死机", "无响应", "报错", "启动失败"]},
        {"id": "network", "name": "网络与连接", "group": "optimization", "keywords": ["网络", "延迟", "丢包", "断线", "掉线", "重连", "连接失败", "ping"]},
        {"id": "graphics_loading", "name": "画面与加载", "group": "optimization", "keywords": ["贴图", "加载", "画质", "渲染", "光影", "视距", "锯齿", "分辨率", "模糊"]},
        {"id": "device_compatibility", "name": "设备适配", "group": "optimization", "keywords": ["适配", "兼容", "安卓", "android", "ios", "iphone", "ipad", "鸿蒙", "模拟器", "显卡", "驱动", "机型"]},
        {"id": "optimization_general", "name": "其他优化提及", "group": "optimization", "keywords": ["优化", "性能", "内存", "闪屏", "体验修复"]},
    ],
}

OUTPUT_COLUMNS = ["post_id", "comment_id", "parent_id", "root_id", "created_at", "text", "like_count", "reply_count", "author_hash", "source_url", "collected_at"]


def _load_config(config_path: Path | None) -> dict[str, Any]:
    if config_path is None:
        candidate = Path(__file__).resolve().parents[2] / "config" / "topics.json"
        config = json.loads(candidate.read_text(encoding="utf-8-sig")) if candidate.exists() else DEFAULT_CONFIG
    else:
        config = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    topics = config.get("topics")
    if not isinstance(topics, list) or not topics:
        raise ValueError("主题配置必须包含非空 topics 列表。")
    ids: set[str] = set()
    groups = config.get("groups", {"character": "角色反馈", "optimization": "优化相关"})
    if not isinstance(groups, dict):
        raise ValueError("主题配置的 groups 必须是对象。")
    for topic in topics:
        if not isinstance(topic, dict):
            raise ValueError("每个主题必须是对象。")
        ident = topic.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", ident) or ident in ids:
            raise ValueError("主题 id 必须唯一，并由小写字母、数字或下划线组成。")
        ids.add(ident)
        if not isinstance(topic.get("name"), str) or not topic["name"].strip():
            raise ValueError("每个主题必须提供名称。")
        if topic.get("group") not in groups:
            raise ValueError(f"主题 {ident} 的 group 不在 groups 中。")
        words = topic.get("keywords")
        if not isinstance(words, list) or not words or any(not isinstance(word, str) or not word.strip() for word in words):
            raise ValueError(f"主题 {ident} 必须有非空关键词列表。")
    return config


def _parse_time(value: str) -> datetime | None:
    """ISO timestamps and Unix seconds/milliseconds; naive times mean UTC+8."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{10}(?:\.\d+)?|\d{13}", value):
            number = float(value)
            return datetime.fromtimestamp(number / 1000 if number > 10**12 else number, tz=timezone.utc).astimezone(LOCAL_TZ)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace("/", "-"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=LOCAL_TZ)
        return parsed.astimezone(LOCAL_TZ)
    except (ValueError, OverflowError, OSError):
        return None


def _csv_safe(value: Any) -> Any:
    """Escape spreadsheet formulas in text, including whitespace-prefixed payloads."""
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + value
    if isinstance(value, str) and value.startswith(("\t", "\r")):
        return "'" + value
    return value


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_safe(row.get(key, "")) for key in fields})


def _read_metadata(output_dir: Path, input_csv: Path, filename: str) -> dict[str, Any]:
    for folder in (output_dir, input_csv.parent):
        path = folder / filename
        if path.exists():
            try:
                obj = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(obj, dict):
                    return obj
            except (ValueError, OSError):
                pass
    return {}


def _keyword_hits(text: str, keywords: list[str]) -> list[str]:
    folded = text.casefold()
    hits = []
    for word in keywords:
        word_folded = word.casefold()
        # English acronyms should not match within unrelated English words.
        if word_folded.isascii() and word_folded.isalnum():
            # Permit common device/performance forms such as 60fps, iOS18,
            # and iPhone16, while avoiding matches inside unrelated words.
            matched = re.search(r"(?<![a-z])" + re.escape(word_folded) + r"(?![a-z])", folded)
        else:
            matched = word_folded in folded
        if matched:
            hits.append(word)
    return hits


def _review_sample(rows: list[dict[str, Any]], topic_ids: list[str], limit: int = 60) -> list[dict[str, Any]]:
    """Deterministic coverage sample; explicitly not a probability sample."""
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    # Include topic examples, overlapping labels and unmatched comments.
    pools = [[i for i, row in enumerate(rows) if tid in row["_topic_ids"]] for tid in topic_ids]
    pools.extend([[i for i, row in enumerate(rows) if len(row["_topic_ids"]) > 1], [i for i, row in enumerate(rows) if not row["_topic_ids"]], list(range(len(rows)))])
    position = 0
    while len(selected) < min(limit, len(rows)):
        added = False
        for pool in pools:
            if position < len(pool) and pool[position] not in seen:
                index = pool[position]
                seen.add(index)
                row = rows[index]
                selected.append({
                    "post_id": row.get("post_id", ""), "comment_id": row.get("comment_id", ""),
                    "text": row.get("text", ""), "rule_topics": row["topic_names"],
                    "matched_keywords": row["matched_keywords"], "manual_topics": "",
                    "contains_explicit_problem": "", "manual_notes": "", "review_status": "待人工核验",
                    "source_url": row.get("source_url", ""),
                })
                added = True
                if len(selected) >= limit:
                    break
        position += 1
        if not added and all(position >= len(pool) for pool in pools):
            break
    return selected


def analyze_comments(input_csv: Path, output_dir: Path, config_path: Path | None = None, split_date: str | None = None) -> dict[str, Any]:
    """Analyze a cleaned CSV and write inspectable research artifacts.

    Every topic's denominator is the total input row count, not total labels.
    A comment may mention multiple topics, so percentages need not sum to 100%.
    ``split_date`` uses China local dates: before < date, after >= date.
    """
    input_csv, output_dir = Path(input_csv), Path(output_dir)
    config = _load_config(config_path)
    boundary = None
    if split_date:
        try:
            boundary = date.fromisoformat(split_date)
        except ValueError as exc:
            raise ValueError("split_date 必须为 YYYY-MM-DD 格式的有效日期。") from exc
    with input_csv.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        if "text" not in fields:
            raise ValueError("输入 CSV 必须包含 text 列；无数据时也请保留表头。")
        rows = [{key: value or "" for key, value in row.items() if key is not None} for row in reader]
    output_dir.mkdir(parents=True, exist_ok=True)
    topics = config["topics"]
    group_names = config.get("groups", {"character": "角色反馈", "optimization": "优化相关"})
    topic_counts: Counter[str] = Counter()
    group_counts: Counter[str] = Counter()
    daily: dict[str, dict[str, Any]] = {}
    segments: dict[str, dict[str, Any]] = {
        "before": {"total_comments": 0, "matched_comments": 0, "topic_counts": Counter()},
        "after": {"total_comments": 0, "matched_comments": 0, "topic_counts": Counter()},
    }
    valid_dates: list[str] = []
    matched_count = 0
    multi_count = 0
    unknown_time_count = 0
    for row in rows:
        detected: list[str] = []
        detected_names: list[str] = []
        detected_groups: set[str] = set()
        all_hits: list[str] = []
        for topic in topics:
            hits = _keyword_hits(row.get("text", ""), topic["keywords"])
            if hits:
                detected.append(topic["id"])
                detected_names.append(topic["name"])
                detected_groups.add(topic["group"])
                all_hits.extend(hits)
                topic_counts[topic["id"]] += 1
        group_counts.update(detected_groups)
        matched_count += bool(detected)
        multi_count += len(detected) > 1
        row["_topic_ids"] = detected
        row["topic_ids"] = ";".join(detected)
        row["topic_names"] = "；".join(detected_names)
        row["matched_keywords"] = "；".join(dict.fromkeys(all_hits))
        row["label_status"] = "规则提及，待人工核验" if detected else "未命中词典，不代表没有相关反馈"
        parsed_time = _parse_time(row.get("created_at", ""))
        row["local_date"] = parsed_time.date().isoformat() if parsed_time else ""
        if parsed_time:
            day = row["local_date"]
            valid_dates.append(day)
            if day not in daily:
                daily[day] = {"date": day, "comment_count": 0, "matched_comment_count": 0, "character_feedback_count": 0, "optimization_count": 0}
            daily[day]["comment_count"] += 1
            daily[day]["matched_comment_count"] += bool(detected)
            daily[day]["character_feedback_count"] += "character" in detected_groups
            daily[day]["optimization_count"] += "optimization" in detected_groups
            if boundary:
                segment = segments["before" if parsed_time.date() < boundary else "after"]
                segment["total_comments"] += 1
                segment["matched_comments"] += bool(detected)
                segment["topic_counts"].update(detected)
        else:
            unknown_time_count += 1

    total = len(rows)
    topic_results = [
        {"id": topic["id"], "name": topic["name"], "group": topic["group"], "comment_count": topic_counts[topic["id"]], "share_of_all": round(topic_counts[topic["id"]] / total, 6) if total else 0.0}
        for topic in topics
    ]
    groups = [
        {"id": group, "name": name, "comment_count": group_counts[group], "share_of_all": round(group_counts[group] / total, 6) if total else 0.0}
        for group, name in group_names.items()
    ]
    comparison = None
    if boundary:
        comparison = {"split_date": boundary.isoformat(), "timezone": TIMEZONE_LABEL, "definition": "前段 < 分界日；后段 ≥ 分界日；按各段评论数分别计算比例", "excluded_unknown_time": unknown_time_count}
        for key, segment in segments.items():
            segment_total = segment["total_comments"]
            comparison[key] = {
                "total_comments": segment_total, "matched_comments": segment["matched_comments"],
                "topics": [{"id": t["id"], "name": t["name"], "comment_count": segment["topic_counts"][t["id"]], "share": round(segment["topic_counts"][t["id"]] / segment_total, 6) if segment_total else 0.0} for t in topics],
            }

    manifest = _read_metadata(output_dir, input_csv, "collection_manifest.json")
    cleaning = _read_metadata(output_dir, input_csv, "cleaning_summary.json")
    # A stale manifest must never silently turn tagged demo rows into live data.
    csv_kinds = {row.get("dataset_kind", "").strip() for row in rows} - {""}
    metadata_kinds = {str(manifest.get("dataset_kind", "")), str(cleaning.get("dataset_kind", ""))} - {""}
    all_kinds = csv_kinds | metadata_kinds
    if "mixed" in all_kinds or {"demo", "live"} <= all_kinds:
        kind = "mixed"
    elif "demo" in all_kinds:
        kind = "demo"
    elif csv_kinds - {"live"}:
        kind = "unverified"
    elif "live" in all_kinds:
        kind = "live"
    else:
        kind = "unverified"
    review = _review_sample(rows, [t["id"] for t in topics])
    limitations = [
        "样本来自选定帖子及采集范围，不能代表所有库街区用户或鸣潮玩家；评论条数不等于玩家人数。",
        "关键词规则仅识别主题提及：肯定、否定、反问和转述均可能命中；提及不等于问题成立或真实诉求。",
        "一条评论可命中多个主题；每个主题占比以全部纳入评论数为分母，各项占比之和可能超过 100%。",
        "未命中不代表没有相关反馈，词典可能遗漏别称、缩写、讽刺与上下文；没有进行情绪或满意度评分。",
        "人工核验样本按主题覆盖选取，不是随机抽样；尚未完成人工标注，不能报告分类准确率。",
        "时间对比受帖子选择、采集上限与讨论者变化影响，不能据此推断版本或角色调整的因果效果。",
    ]
    if kind == "demo":
        limitations.insert(0, "这是合成演示数据，仅验证流程与展示效果，不是实际玩家研究结论。")
    elif kind == "mixed":
        limitations.insert(0, "检测到混合或冲突的数据来源标记，可能包含演示数据；请分离数据后重新分析，不能用于真实玩家研究结论。")
    elif kind == "unverified":
        limitations.insert(0, "数据来源类型未标注，需核实采集记录后再用于研究结论。")
    summary = {
        "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset_kind": kind, "source_kind_evidence": {"csv": sorted(csv_kinds), "metadata": sorted(metadata_kinds)}, "input_file": input_csv.name, "timezone": TIMEZONE_LABEL,
        "total_comments": total, "matched_comments": matched_count, "unmatched_comments": total - matched_count,
        "multi_topic_comments": multi_count,
        "topic_counts": topic_results, "groups": groups,
        "date_range": {"start": min(valid_dates) if valid_dates else None, "end": max(valid_dates) if valid_dates else None},
        "unknown_time_comments": unknown_time_count, "daily": [daily[key] for key in sorted(daily)],
        "split_comparison": comparison, "review_sample_size": len(review), "human_review_completed": False,
        "methodology": {"labeling": "可编辑关键词词典，多标签主题提及", "sentiment": "未执行", "denominator": "全部纳入评论数", "naive_timestamp_assumption": "无时区时间按 UTC+08:00 解释", "config": config, "review_sampling": "确定性主题覆盖抽样，最多 60 条，包含命中、交叉命中与未命中样本"},
        "limitations": limitations,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(output_dir / "topics.csv", ["id", "name", "group", "comment_count", "share_of_all"], topic_results)
    _write_csv(output_dir / "daily_counts.csv", ["date", "comment_count", "matched_comment_count", "character_feedback_count", "optimization_count"], summary["daily"])
    label_fields = list(dict.fromkeys(OUTPUT_COLUMNS + fields + ["local_date", "topic_ids", "topic_names", "matched_keywords", "label_status"]))
    _write_csv(output_dir / "labeled_comments.csv", label_fields, rows)
    _write_csv(output_dir / "review_sample.csv", ["post_id", "comment_id", "text", "rule_topics", "matched_keywords", "manual_topics", "contains_explicit_problem", "manual_notes", "review_status", "source_url"], review)

    ranked = sorted(topic_results, key=lambda item: item["comment_count"], reverse=True)
    lines = ["# 玩家评论主题分析", "", f"数据类型：{kind}。纳入 {total} 条评论，主题规则命中 {matched_count} 条，交叉命中 {multi_count} 条。", "", "## 可由本次样本支持的描述", ""]
    if not total:
        lines.append("当前没有可分析的评论。请采集或导入评论后重新运行；零样本不能支持玩家反馈结论。")
    elif kind in {"demo", "mixed"}:
        lines.append("以下数据包含或可能包含合成演示内容，不能写为实际玩家研究发现。")
    for item in ranked[:5] if total else []:
        lines.append(f"- “{item['name']}”被规则识别为 {item['comment_count']} 条提及，占全部评论 {item['share_of_all']:.1%}；需要人工核验语境。")
    lines.extend(["", "## 建议的后续研究", "", "1. 在 review_sample.csv 中填写 manual_topics、contains_explicit_problem 和 manual_notes；先确认原文是否真的表达问题。", "2. 对核实的问题按角色、设备、场景与版本补充记录；将新词与排除规则加入配置并复跑。", "3. 通过访谈或可用性测试核实原因；提出建议时附证据、适用范围与验证方案。", "", "## 局限与统计口径", ""])
    lines.extend("- " + limitation for limitation in limitations)
    lines.append(f"- 日统计使用{TIMEZONE_LABEL}；无时区时间按该时区解释；{unknown_time_count} 条缺失或无效时间未纳入日统计。")
    (output_dir / "key_findings.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
