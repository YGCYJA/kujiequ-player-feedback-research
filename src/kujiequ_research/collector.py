from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import secrets
from typing import Any

from .client import KuroClient
from .models import PostTarget, ResearchError, SchemaChanged, first, identifier, normalize_comment, now_iso


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


class RunStore:
    def __init__(self, directory: Path, *, resume: bool, scope: dict):
        self.directory = Path(directory)
        self.raw_path = self.directory / "comments.jsonl"
        self.checkpoint_path = self.directory / "checkpoint.json"
        self.manifest_path = self.directory / "collection_manifest.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        existing = self.raw_path.exists() or self.checkpoint_path.exists() or self.manifest_path.exists()
        if existing and not resume:
            raise ResearchError("输出目录已有采集结果。请加 --resume 接着采集，或选一个新的输出目录。")
        self.rows: list[dict] = []
        self.seen: set[tuple[str, str]] = set()
        self.counts: Counter = Counter()
        if resume and existing:
            if not self.checkpoint_path.exists() or not self.manifest_path.exists() or not self.raw_path.exists():
                raise ResearchError("续采目录缺少 comments.jsonl、checkpoint.json 或 collection_manifest.json；请恢复原文件或使用新目录。")
            self.checkpoint = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if self.manifest.get("dataset_kind") != "live" or self.manifest.get("scope") != scope:
                raise ResearchError("续采的帖子、排序或回复范围与原采集不一致，请使用新目录。")
            allowed_posts = {str(item["post_id"]) for item in scope["posts"]}
            for line_number, line in enumerate(self.raw_path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if (not isinstance(row, dict) or row.get("dataset_kind") != "live"
                            or identifier(row.get("post_id")) not in allowed_posts
                            or not identifier(row.get("comment_id")) or not isinstance(row.get("text"), str)):
                        raise ValueError("row provenance or schema mismatch")
                    self._remember(row)
                except (ValueError, KeyError, TypeError, SchemaChanged) as exc:
                    raise ResearchError(f"comments.jsonl 第 {line_number} 行损坏或来源不一致，请保留文件并修复后续采。") from exc
            expected = self.manifest.get("total_comments")
            if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0 or len(self.rows) < expected:
                raise ResearchError("原始评论记录少于采集清单或清单计数无效，可能有文件丢失；已停止续采。")
            # More rows than the manifest can legitimately result from an
            # interruption after appending a page but before committing state.
        else:
            self.checkpoint = {"format_version": 1, "posts": {}}
            self.manifest = {
                "dataset_kind": "live", "started_at": now_iso(), "scope": scope,
                "status": "initialized", "posts": {},
                "sampling_note": "用户指定帖子内按所选排序进行有上限采集；不是随机抽样，不代表全部玩家。",
                "privacy_note": "仅保存研究所需字段；作者ID经本地随机盐HMAC处理；不保存用户名、头像或IP属地。评论正文仍可能包含自述信息，公开前须人工检查。",
            }
        salt_path = self.directory / ".author_salt"
        if salt_path.exists():
            self.salt = salt_path.read_bytes()
            if len(self.salt) != 32:
                raise ResearchError("本地匿名化盐文件格式异常。")
        elif self.rows:
            raise ResearchError("续采缺少 .author_salt，不能保持作者匿名标识一致，请恢复该文件或使用新目录。")
        else:
            self.salt = secrets.token_bytes(32)
            salt_path.write_bytes(self.salt)
        self.raw_path.touch(exist_ok=True)

    def _remember(self, row: dict) -> None:
        key = (str(row["post_id"]), str(row["comment_id"]))
        if key not in self.seen:
            self.seen.add(key)
            self.rows.append(row)
            self.counts[key[0]] += 1

    def add(self, row: dict) -> bool:
        key = (row["post_id"], row["comment_id"])
        if key in self.seen:
            return False
        with self.raw_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
        self._remember(row)
        return True

    def save(self) -> None:
        write_json(self.checkpoint_path, self.checkpoint)
        self.manifest.update({
            "updated_at": now_iso(), "total_comments": len(self.rows),
            "root_comments": sum(not bool(row.get("parent_id")) for row in self.rows),
            "replies": sum(bool(row.get("parent_id")) for row in self.rows),
        })
        write_json(self.manifest_path, self.manifest)


def collect_comments(client: KuroClient, targets: list[PostTarget], directory: Path, *,
                     max_comments: int = 100, max_pages: int = 20, page_size: int = 20,
                     include_replies: bool = True, order: int = 3, resume: bool = False) -> dict:
    """Limits apply per post, counting replies; never equate a cap with completeness."""
    if not targets:
        raise ResearchError("至少提供一个帖子。")
    if max_comments < 1 or max_pages < 1 or not 1 <= page_size <= 50 or order not in {2, 3}:
        raise ResearchError("评论/页数上限须为正数；每页1–50条；排序为2默认或3最新。")
    targets = list(dict.fromkeys(targets))
    scope = {
        "posts": [{"post_id": t.post_id, "game_id": t.game_id, "source_url": t.url} for t in targets],
        "include_replies": include_replies, "order": order, "page_size": page_size,
    }
    store = RunStore(directory, resume=resume, scope=scope)
    store.manifest["status"] = "running"
    store.manifest["limits_per_post"] = {"comments": max_comments, "pages_per_invocation": max_pages}
    store.save()
    try:
        for target in targets:
            state = store.checkpoint["posts"].setdefault(target.post_id, {
                "next_page": 1, "roots_done": False, "pending_replies": {},
                "last_main_ids": [], "pages_collected": 0, "inferred_pagination_pages": 0,
            })
            pages_this_run = 0
            stop_reason = "complete_within_scope"

            def at_limit() -> str:
                if store.counts[target.post_id] >= max_comments:
                    return "comment_limit"
                if pages_this_run >= max_pages:
                    return "page_limit"
                return ""

            def append(obj: dict, root_id: str = "") -> bool:
                row = normalize_comment(obj, target, store.salt, root_id=root_id)
                if (row["post_id"], row["comment_id"]) in store.seen:
                    return True
                if store.counts[target.post_id] >= max_comments:
                    return False
                store.add(row)
                return True

            def drain_replies() -> str:
                nonlocal pages_this_run
                if not include_replies:
                    return ""
                for root_id, progress in state["pending_replies"].items():
                    while not progress.get("done", False):
                        if reason := at_limit():
                            return reason
                        result = client.replies(target, root_id, progress["next_page"], page_size)
                        pages_this_run += 1
                        state["pages_collected"] += 1
                        state["inferred_pagination_pages"] += int(result.inferred_pagination)
                        ids = [identifier(first(obj, "replyId", "id", "commentId")) for obj in result.items]
                        if result.has_next and (not ids or ids == progress.get("last_ids")):
                            raise SchemaChanged("回复分页没有前进，已停止并保留进度。")
                        for obj in result.items:
                            if not append(obj, root_id):
                                return "comment_limit"
                        progress.update(next_page=progress["next_page"] + 1, done=not result.has_next, last_ids=ids)
                        store.save()
                return ""

            # Finish saved reply work before moving to new root pages.
            stop_reason = drain_replies()
            while not stop_reason and not state["roots_done"]:
                if stop_reason := at_limit():
                    break
                result = client.comments(target, state["next_page"], page_size, order)
                pages_this_run += 1
                state["pages_collected"] += 1
                state["inferred_pagination_pages"] += int(result.inferred_pagination)
                ids = [identifier(first(obj, "commentId", "postCommentId", "id")) for obj in result.items]
                if result.has_next and (not ids or ids == state.get("last_main_ids")):
                    raise SchemaChanged("评论分页没有前进，已停止并保留进度。")
                page_finished = True
                for obj in result.items + result.highlights:
                    root_id = identifier(first(obj, "commentId", "postCommentId", "id"))
                    if not append(obj):
                        page_finished = False
                        break
                    if include_replies:
                        previews = obj.get("replyVos", []) or []
                        if not isinstance(previews, list):
                            raise SchemaChanged("replyVos 不是数组，无法可靠采集楼中楼。")
                        if previews or int(obj.get("replyCount") or 0) > 0:
                            state["pending_replies"].setdefault(root_id, {"next_page": 1, "done": False, "last_ids": []})
                        for reply in previews:
                            if not isinstance(reply, dict):
                                raise SchemaChanged("内联回复格式变化。")
                            if not append(reply, root_id):
                                page_finished = False
                                break
                    if not page_finished:
                        break
                if not page_finished:
                    stop_reason = "comment_limit"
                    break  # Replay this page on resume; stored IDs prevent duplicates.
                state["next_page"] += 1
                state["roots_done"] = not result.has_next
                state["last_main_ids"] = ids
                store.save()
                stop_reason = drain_replies()
            complete = state["roots_done"] and (not include_replies or all(x.get("done") for x in state["pending_replies"].values()))
            store.manifest["posts"][target.post_id] = {
                "source_url": target.url, "comments_saved": store.counts[target.post_id],
                "complete_within_scope": complete,
                "stop_reason": "complete_within_scope" if complete else (stop_reason or "not_finished"),
                "pages_collected": state["pages_collected"],
                "inferred_pagination_pages": state["inferred_pagination_pages"],
            }
            store.save()
        store.manifest["status"] = "complete_within_scope" if all(
            item["complete_within_scope"] for item in store.manifest["posts"].values()
        ) else "limited"
    except (Exception, KeyboardInterrupt) as exc:
        store.manifest["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        store.manifest["error"] = str(exc) if isinstance(exc, ResearchError) else type(exc).__name__
        if "target" in locals():
            state = store.checkpoint["posts"].get(target.post_id, {})
            store.manifest["posts"][target.post_id] = {
                "source_url": target.url, "comments_saved": store.counts[target.post_id],
                "complete_within_scope": False, "stop_reason": store.manifest["status"],
                "pages_collected": state.get("pages_collected", 0),
                "inferred_pagination_pages": state.get("inferred_pagination_pages", 0),
            }
        store.manifest["last_invocation_requests"] = client.request_count
        store.save()
        raise
    store.manifest["last_invocation_requests"] = client.request_count
    store.manifest.pop("error", None)
    store.save()
    return store.manifest
