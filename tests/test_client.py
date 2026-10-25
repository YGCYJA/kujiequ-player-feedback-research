"""Offline contract tests: no real platform traffic or credentials required."""

import json
from datetime import datetime

import pytest
import requests

from kujiequ_research.client import BASE_URL, KuroClient, parse_page
from kujiequ_research.models import (
    SHANGHAI, AccessLimited, PostTarget, ResearchError, SchemaChanged,
    identifier, normalize_comment, normalize_time, optional_count, parse_post,
    rich_text,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.now += seconds


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self.status_code = status
        self.payload = payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, responses, clock):
        self.responses = iter(responses)
        self.clock = clock
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, "at": self.clock(), **kwargs})
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def comments_payload(items=None, **data):
    return {"code": 200, "data": {"postCommentList": items or [], "hasNext": 0, **data}}


def fake_client(responses, **kwargs):
    clock = FakeClock()
    session = FakeSession(responses, clock)
    client = KuroClient(session=session, clock=clock, sleep=clock.sleep, **kwargs)
    return client, session, clock


def test_normalize_large_ids_rich_text_milliseconds_and_identity_minimization():
    target = PostTarget("1900000000000000001")
    millis = int(datetime(2026, 10, 25, 12, 30, tzinfo=SHANGHAI).timestamp() * 1000)
    raw = {
        "commentId": 1900000000000000002, "userId": "1900000000000000003",
        "userName": "不可导出的昵称", "ip": "不要导出IP", "createTime": millis,
        "commentContent": json.dumps([
            {"contentType": 1, "content": "角色手感不流畅 😢"},
            {"contentType": 2, "content": "https://example.invalid/image.png"},
        ], ensure_ascii=False),
    }
    result = normalize_comment(raw, target, b"offline-test-salt")
    assert result["post_id"] == "1900000000000000001"
    assert result["comment_id"] == result["root_id"] == "1900000000000000002"
    assert result["created_at"] == "2026-10-25T12:30:00+08:00"
    assert result["text"] == "角色手感不流畅 😢\n[图片]"
    assert "image.png" not in result["text"]
    assert result["like_count"] == result["reply_count"] == ""
    assert len(result["author_hash"]) == 20
    assert result["author_hash"] != raw["userId"]
    assert not {"userId", "userName", "ip"}.intersection(result)
    assert result["dataset_kind"] == "live"
    assert normalize_comment(raw, target, b"another-salt")["author_hash"] != result["author_hash"]


def test_reply_preserves_root_parent_and_reply_id():
    row = normalize_comment({
        "replyId": "1900000000000000004", "postCommentReplayId": "0",
        "replyContentStr": "优化卡顿", "replyTime": "2026-10-25 12:30:00",
        "likeCount": 0,
    }, PostTarget("1900000000000000001"), b"test", root_id="1900000000000000002")
    assert row["comment_id"] == "1900000000000000004"
    assert row["root_id"] == row["parent_id"] == "1900000000000000002"
    assert row["like_count"] == 0
    assert row["reply_count"] == ""
    assert row["author_hash"] == ""
    assert row["text"] == "优化卡顿"


def test_rich_text_children_and_plain_text_do_not_strip_negation():
    assert rich_text({"children": [{"text": "不"}, {"text": "推荐"}]}) == "不推荐"
    assert rich_text("不是很顺畅 👍") == "不是很顺畅 👍"
    assert rich_text({"type": "image", "url": "https://example.invalid/photo"}) == "[图片]"
    assert rich_text("[非JSON的玩家文字]") == "[非JSON的玩家文字]"


def test_time_normalization_keeps_day_precision_and_does_not_invent_relative_dates():
    assert normalize_time("2026-10-25T04:30:00Z") == "2026-10-25T12:30:00+08:00"
    assert normalize_time("2026/10/25") == "2026-10-25"
    assert normalize_time("昨天") == ""
    assert normalize_time("2026-02-30") == ""
    assert normalize_time(None) == ""
    assert normalize_time(1) == ""


def test_unknown_like_count_is_not_zero():
    assert optional_count(None) == optional_count("") == ""
    assert optional_count("unknown") == ""
    assert optional_count("0") == 0


@pytest.mark.parametrize("value", [True, 1.900000000000000001e18])
def test_unsafe_id_types_are_rejected(value):
    with pytest.raises(SchemaChanged):
        identifier(value)


def test_post_links_keep_exact_ids_and_validate_origin():
    post_id = "1900000000000000001"
    assert parse_post(post_id).post_id == post_id
    assert parse_post(f"https://www.kurobbs.com/mc/post/{post_id}").url.endswith(post_id)
    assert parse_post(f"https://www.kurobbs.com/pns/post/{post_id}").game_id == 2
    with pytest.raises(ResearchError):
        parse_post(f"https://example.invalid/mc/post/{post_id}")


def test_comment_request_uses_verified_endpoint_and_show_order_type(monkeypatch):
    monkeypatch.delenv("KURO_TOKEN", raising=False)
    client, session, _ = fake_client([FakeResponse(comments_payload())])
    target = PostTarget("1900000000000000001")
    page = client.comments(target, page=2, page_size=10, order=3)
    call = session.calls[0]
    assert not page.has_next
    assert call["url"] == BASE_URL + "/forum/comment/getPostCommentListV2"
    assert call["data"] == {
        "postId": target.post_id, "showOrderType": 3, "isOnlyPublisher": 0,
        "pageIndex": 2, "pageSize": 10,
    }
    assert "orderType" not in call["data"]
    assert call["headers"]["Referer"] == target.url
    assert "token" not in call["headers"]
    assert call["allow_redirects"] is False


def test_short_page_with_explicit_has_next_continues_and_keeps_highlights():
    client, session, _ = fake_client([
        FakeResponse(comments_payload([{"commentId": "1"}], hasNext=1, stickComments=[{"commentId": "hot"}])),
        FakeResponse(comments_payload([{"commentId": "2"}], hasNext=0)),
    ])
    target = PostTarget("1900000000000000001")
    collected, page_number = [], 1
    while True:
        page = client.comments(target, page=page_number, page_size=20)
        collected.extend(page.items)
        if page_number == 1:
            assert page.highlights == [{"commentId": "hot"}]
            assert page.has_next is True
            assert page.inferred_pagination is False
        if not page.has_next:
            break
        page_number += 1
    assert [item["commentId"] for item in collected] == ["1", "2"]
    assert len(session.calls) == 2
    assert [call["data"]["pageIndex"] for call in session.calls] == [1, 2]


def test_reply_request_and_page_contract():
    client, session, _ = fake_client([FakeResponse({"code": "200", "data": {"replys": [{"replyId": "3"}], "hasNext": "1"}})])
    page = client.replies(PostTarget("1"), root_id="1900000000000000002", page=2, page_size=10)
    assert page.has_next is True
    assert session.calls[0]["url"].endswith("/forum/comment/getReplyList")
    assert session.calls[0]["data"]["postCommentId"] == "1900000000000000002"


@pytest.mark.parametrize("payload", [
    {}, {"data": []}, {"data": {}}, {"data": {"postCommentList": ["invalid"]}},
    {"data": {"postCommentList": [], "hasNext": "maybe"}},
    {"data": {"postCommentList": [], "hotComments": "invalid"}},
])
def test_unknown_page_schema_raises_instead_of_reporting_empty(payload):
    with pytest.raises(SchemaChanged):
        parse_page(payload)


def test_missing_has_next_marks_inference_explicitly():
    page = parse_page({"data": {"postCommentList": [{"commentId": "1"}]}}, page_size=1)
    assert page.has_next is True
    assert page.inferred_pagination is True


@pytest.mark.parametrize("response", [
    FakeResponse(status=429),
    FakeResponse({"code": 220, "msg": "access limited"}),
])
def test_access_restrictions_do_not_retry(response):
    client, session, clock = fake_client([response], retries=3)
    with pytest.raises(AccessLimited):
        client.comments(PostTarget("1"), page=1, page_size=20)
    assert len(session.calls) == client.request_count == 1
    assert clock.sleeps == []


def test_503_retries_with_backoff_and_pacing():
    client, session, clock = fake_client([
        FakeResponse(status=503), FakeResponse(comments_payload()),
    ], delay=1.5, retries=2)
    result = client.comments(PostTarget("1"), page=1, page_size=20)
    assert result.items == []
    assert len(session.calls) == client.request_count == 2
    assert session.calls[1]["at"] - session.calls[0]["at"] >= 2
    assert sum(clock.sleeps) >= 2


def test_503_retry_limit_is_bounded():
    client, session, _ = fake_client([FakeResponse(status=503)] * 3, retries=2)
    with pytest.raises(ResearchError, match="503"):
        client.comments(PostTarget("1"), page=1, page_size=20)
    assert len(session.calls) == 3


def test_separate_requests_are_throttled():
    client, session, clock = fake_client([
        FakeResponse(comments_payload()), FakeResponse(comments_payload()),
    ], delay=1.5)
    target = PostTarget("1")
    client.comments(target, page=1, page_size=20)
    clock.now += 0.25
    client.comments(target, page=2, page_size=20)
    assert session.calls[1]["at"] - session.calls[0]["at"] >= 1.5
    assert clock.sleeps == [1.25]


def test_connection_failure_retries_then_reports_actionable_error():
    client, session, _ = fake_client([requests.ConnectionError("offline")] * 2, retries=1)
    with pytest.raises(ResearchError, match="网络连接失败"):
        client.comments(PostTarget("1"), page=1, page_size=20)
    assert len(session.calls) == 2


@pytest.mark.parametrize("payload", [[], {"data": {}}, ValueError("HTML body")])
def test_invalid_response_body_raises_schema_error(payload):
    client, session, _ = fake_client([FakeResponse(payload)])
    with pytest.raises(SchemaChanged):
        client.comments(PostTarget("1"), page=1, page_size=20)
    assert len(session.calls) == 1


def test_non_read_endpoint_is_never_requested():
    client, session, _ = fake_client([])
    with pytest.raises(ResearchError, match="仅允许读取"):
        client.request("/forum/comment/create", {}, PostTarget("1"))
    assert session.calls == []
