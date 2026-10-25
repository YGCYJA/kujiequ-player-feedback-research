"""Synthetic examples for learning; never passed off as player observations."""
from __future__ import annotations

import json
from pathlib import Path

from .collector import write_json
from .models import now_iso

EXAMPLE_TEXTS = [
    "【模拟】示例角色A的共鸣技能说明看不懂，想看连招演示。",
    "【模拟】这次角色手感不错，闪避接技能很顺。",
    "【模拟】声骸养成素材不够，培养成本有点高。",
    "【模拟】角色建模和配音都喜欢，希望增加角色剧情。",
    "【模拟】锁敌会切到远处，打击感也需要调整。",
    "【模拟】新武器的倍率是否过高？担心数值膨胀。",
    "【模拟】开大招时掉帧，安卓手机玩十分钟就发热。",
    "【模拟】更新后进入地图闪退，重新安装也没解决。",
    "【模拟】贴图加载慢，移动后画面模糊。",
    "【模拟】WiFi下延迟也很高，经常断线重连。",
    "【模拟】iOS18适配后已经不卡顿了。",
    "【模拟】60fps稳定，流畅了很多。",
    "【模拟】手机功耗高、耗电快，希望降低温度。",
    "【模拟】显卡驱动更新后启动失败，有兼容问题。",
    "【模拟】内存占用下降了，这次优化有效果。",
    "【模拟】欢迎大家交流角色机制和循环。",
    "【模拟】操作难但伤害高，不确定适不适合新手。",
    "【模拟】武器突破素材获得方式太分散。",
    "【模拟】希望优化模拟器的分辨率和锯齿。",
    "【模拟】谢谢整理，已经收藏了。",
    "【模拟】画质调低依然黑屏，加载到一半无响应。",
    "【模拟】角色动作前摇太长，后摇取消不直观。",
    "【模拟】并不是所有设备都有问题，我这里运行正常。",
    "【模拟】剧情演出中出现闪屏，但普通跑图正常。",
]


def create_demo(directory: Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "collection_manifest.json"
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("dataset_kind") != "demo":
            raise ValueError("该目录包含真实或未确认来源的数据，请给演示指定新目录。")
    path = directory / "demo_comments.jsonl"
    rows = []
    for index, text in enumerate(EXAMPLE_TEXTS, 1):
        rows.append({
            "post_id": "demo-character" if index % 2 else "demo-optimization",
            "comment_id": f"demo-{index:03}", "parent_id": "", "root_id": f"demo-{index:03}",
            "created_at": f"2026-10-{20 + index % 4:02}T{8 + index % 12:02}:00:00+08:00",
            "text": text, "like_count": index % 7, "reply_count": 0,
            "author_hash": f"demo-author-{index:03}", "source_url": "", "collected_at": now_iso(),
            "dataset_kind": "demo",
        })
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_json(manifest_path, {
        "dataset_kind": "demo", "status": "synthetic_demo", "total_comments": len(rows),
        "started_at": now_iso(), "sampling_note": "全部为程序内置模拟评论，仅演示流程；不可作为真实玩家证据或简历研究成果。",
    })
    return path

