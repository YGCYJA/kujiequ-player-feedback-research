from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
import re
from typing import Any
from urllib.parse import urlparse

SHANGHAI = timezone(timedelta(hours=8))


class ResearchError(Exception):
    """An actionable error safe to show to the user."""


class AccessLimited(ResearchError):
    """Stop rather than attempting to evade a platform restriction."""


class SchemaChanged(ResearchError):
    """Do not report an unknown response shape as an empty result."""


def now_iso() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


@dataclass(frozen=True)
class PostTarget:
    post_id: str
    game_id: int = 3

    @property
    def url(self) -> str:
        game = "mc" if self.game_id == 3 else "pns"
        return f"https://www.kurobbs.com/{game}/post/{self.post_id}"


def parse_post(value: str, game_id: int = 3) -> PostTarget:
    value = value.strip()
    if re.fullmatch(r"[0-9]{1,30}", value):
        return PostTarget(value, game_id)
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in {"www.kurobbs.com", "kurobbs.com"}:
        raise ResearchError("请填写库街区 https 帖子链接，或纯数字帖子 ID。")
    match = re.fullmatch(r"/(mc|pns)/post/([0-9]{1,30})/?", parsed.path)
    if not match:
        raise ResearchError("链接应类似 https://www.kurobbs.com/mc/post/帖子ID。")
    return PostTarget(match[2], 3 if match[1] == "mc" else 2)


def identifier(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise SchemaChanged("评论标识不是字符串或整数，已停止以避免 ID 精度损失。")
    return str(value)


def first(obj: dict, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in obj and obj[key] is not None and obj[key] != "":
            return obj[key]
    return default


def rich_text(value: Any) -> str:
    """Extract text blocks, without accidentally treating image URLs as text."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("[", "{")):
            try:
                parsed = json.loads(stripped)
            except (ValueError, TypeError):
                return value
            if isinstance(parsed, (list, dict)):
                return rich_text(parsed)
        return value
    if isinstance(value, list):
        return "\n".join(part for item in value if (part := rich_text(item)))
    if isinstance(value, dict):
        if "children" in value:
            children = value["children"]
            if isinstance(children, list):
                return "".join(rich_text(child) for child in children)
        content_type = value.get("contentType", value.get("type", 1))
        if content_type in (2, "2", "image", "img"):
            return "[图片]"
        text = first(value, "text", "content", "value", default="")
        return rich_text(text) if text is not value else ""
    return ""


def normalize_time(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        try:
            number = float(value)
            if number > 100_000_000_000:
                number /= 1000
            if number < 946_684_800 or number > 4_102_444_800:
                return ""
            return datetime.fromtimestamp(number, SHANGHAI).isoformat(timespec="seconds")
        except (ValueError, OverflowError, OSError):
            return ""
    text = str(value).strip().replace("/", "-")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return ""
        return text  # Preserve day precision instead of inventing an exact time.
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        moment = moment.replace(tzinfo=SHANGHAI) if moment.tzinfo is None else moment.astimezone(SHANGHAI)
        return moment.isoformat(timespec="seconds")
    except ValueError:
        return ""  # Relative dates such as "昨天" are not assigned invented dates.


def optional_count(value: Any) -> int | str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return value if value >= 0 else ""
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return ""


def normalize_comment(obj: dict, target: PostTarget, salt: bytes, *, root_id: str = "") -> dict:
    is_reply = bool(root_id)
    raw_id = first(obj, *( ("replyId", "id", "commentId") if is_reply else ("commentId", "postCommentId", "id") ))
    comment_id = identifier(raw_id)
    if not comment_id:
        raise SchemaChanged("评论缺少稳定 ID；已停止，不使用文本冒充评论标识。")
    user = obj.get("user") if isinstance(obj.get("user"), dict) else {}
    user_id = first(obj, "userId", "commentUserId", default=first(user, "userId", "id", default=""))
    author_hash = hmac.new(salt, identifier(user_id).encode(), hashlib.sha256).hexdigest()[:20] if user_id != "" else ""
    parent_id = identifier(first(obj, "postCommentReplayId", "postCommentReplyId", "parentId", default=root_id)) if is_reply else ""
    if parent_id in {"", "0"} and is_reply:
        parent_id = root_id
    content = first(obj, "replyContent", "replyContentStr", "content", default="") if is_reply else first(obj, "commentContent", "content", default="")
    text = rich_text(content)
    if not text and is_reply:
        text = rich_text(obj.get("replyContentStr", ""))
    raw_time = first(obj, "createTime", "createTimestamp", "commentTime", "replyTime", "publishTime", default="")
    created_at = normalize_time(raw_time)
    return {
        "post_id": target.post_id,
        "comment_id": comment_id,
        "parent_id": parent_id,
        "root_id": root_id or comment_id,
        "created_at": created_at,
        "created_at_raw": str(raw_time),
        "time_source": "platform_timestamp_or_full_date" if created_at else "unresolved_display_date",
        "time_precision": "day" if len(created_at) == 10 else ("second" if created_at else "unknown"),
        "text": text,
        "like_count": optional_count(first(obj, "likeCount", "praiseCount")),
        "reply_count": optional_count(first(obj, "replyCount", "commentCount")) if not is_reply else "",
        "author_hash": author_hash,
        "source_url": target.url,
        "collected_at": now_iso(),
        "dataset_kind": "live",
    }

