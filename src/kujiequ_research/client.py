from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

import requests

from .models import AccessLimited, PostTarget, ResearchError, SchemaChanged

BASE_URL = "https://api.kurobbs.com"
READ_ENDPOINTS = {
    "/forum/comment/getPostCommentListV2",
    "/forum/comment/getReplyList",
    "/forum/getPostDetail",
    "/forum/companyEvent/findEventList",
}


@dataclass
class CommentPage:
    items: list[dict]
    has_next: bool
    highlights: list[dict]
    inferred_pagination: bool = False


def as_bool(value: Any) -> bool:
    if value in (True, 1, "1", "true", "True"):
        return True
    if value in (False, 0, "0", "false", "False", None):
        return False
    raise SchemaChanged("hasNext 出现未知值，无法可靠判断分页。")


def parse_page(payload: dict, *, replies: bool = False, page_size: int = 20) -> CommentPage:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise SchemaChanged("接口 data 不是对象，无法可靠解析评论。")
    list_key = "replys" if replies else "postCommentList"
    items = data.get(list_key)
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise SchemaChanged(f"接口缺少 {list_key} 数组，可能已改版。")
    highlights: list[dict] = []
    if not replies:
        for key in ("stickComments", "hotComments"):
            values = data.get(key, []) or []
            if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
                raise SchemaChanged(f"接口 {key} 格式变化。")
            highlights.extend(values)
    inferred = "hasNext" not in data
    return CommentPage(items, len(items) >= page_size if inferred else as_bool(data["hasNext"]), highlights, inferred)


class KuroClient:
    """Read-only endpoints, bounded retries, and one paced request at a time."""

    def __init__(self, *, delay: float = 2.0, timeout: float = 20, retries: int = 2,
                 session: requests.Session | None = None, sleep=time.sleep, clock=time.monotonic):
        if delay < 1 or timeout <= 0 or retries < 0 or retries > 5:
            raise ResearchError("请求间隔至少 1 秒；超时须为正数；重试次数为 0–5。")
        self.delay, self.timeout, self.retries = delay, timeout, retries
        self.session = session or requests.Session()
        self.sleep, self.clock = sleep, clock
        self._last_request: float | None = None
        self.request_count = 0

    def request(self, endpoint: str, form: dict, target: PostTarget) -> dict:
        if endpoint not in READ_ENDPOINTS:
            raise ResearchError("仅允许读取帖子和评论的接口。")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36 KujiequResearch/0.1",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.kurobbs.com",
            "Referer": target.url,
            "source": "h5",
            "version": "3.3.2",
        }
        for attempt in range(self.retries + 1):
            if self._last_request is not None:
                self.sleep(max(0, self.delay - (self.clock() - self._last_request)))
            self._last_request = self.clock()
            self.request_count += 1
            try:
                response = self.session.post(BASE_URL + endpoint, data=form, headers=headers, timeout=self.timeout, allow_redirects=False)
            except requests.RequestException as exc:
                if attempt < self.retries:
                    self.sleep(2 ** (attempt + 1))
                    continue
                raise ResearchError("网络连接失败。请检查 DNS、网络或代理；已保留采集进度。") from exc
            if response.status_code in {401, 403, 412, 429}:
                raise AccessLimited(f"平台限制访问（HTTP {response.status_code}），采集已停止。请在官方页面检查访问状态后再试。")
            if 500 <= response.status_code < 600 and attempt < self.retries:
                self.sleep(2 ** (attempt + 1))
                continue
            if response.status_code != 200:
                raise ResearchError(f"接口返回 HTTP {response.status_code}，采集已停止。")
            try:
                payload = response.json()
            except ValueError as exc:
                raise SchemaChanged("接口未返回 JSON，可能要求网页验证；请到官方页面检查。") from exc
            if not isinstance(payload, dict) or "code" not in payload:
                raise SchemaChanged("接口返回结构变化，缺少 code。")
            code = str(payload["code"])
            message = str(payload.get("msg", ""))
            if code != "200" or payload.get("success") is False:
                if code in {"220", "401", "403", "429"} or any(word in message for word in ("登录", "验证码", "频繁", "风控", "验证", "限制")):
                    raise AccessLimited(f"平台要求登录、验证或限制访问（业务码 {code}），采集已停止；不会自动登录或绕过限制。")
                raise ResearchError(f"接口未成功（业务码 {code}）。请检查帖子是否存在和接口版本。")
            return payload
        raise ResearchError("请求未完成。")

    def comments(self, target: PostTarget, page: int, page_size: int, order: int = 3) -> CommentPage:
        payload = self.request("/forum/comment/getPostCommentListV2", {
            "postId": target.post_id, "showOrderType": order, "isOnlyPublisher": 0,
            "pageIndex": page, "pageSize": page_size,
        }, target)
        return parse_page(payload, page_size=page_size)

    def replies(self, target: PostTarget, root_id: str, page: int, page_size: int) -> CommentPage:
        payload = self.request("/forum/comment/getReplyList", {
            "postId": target.post_id, "postCommentId": root_id,
            "pageIndex": page, "pageSize": page_size,
        }, target)
        return parse_page(payload, replies=True, page_size=page_size)

    def announcements(self, game_id: int, page: int = 1, page_size: int = 20, event_type: int = 3) -> list[dict]:
        if event_type not in {1, 2, 3}:
            raise ResearchError("公告栏目类型应为1活动、2资讯或3公告。")
        payload = self.request("/forum/companyEvent/findEventList", {
            "gameId": game_id, "eventType": event_type, "pageNo": page, "pageSize": page_size,
        }, PostTarget("0", game_id))
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise SchemaChanged("官方公告列表结构变化，缺少 data.list。")
        return data["list"]

