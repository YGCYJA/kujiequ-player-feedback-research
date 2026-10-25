import json

import pytest

from kujiequ_research.client import CommentPage
from kujiequ_research.collector import collect_comments
from kujiequ_research.models import PostTarget, ResearchError, SchemaChanged


def comment(ident, *, replies=0):
    return {"commentId": str(ident), "commentContent": [{"contentType": 1, "content": "角色手感卡顿"}],
            "replyCount": replies, "userId": "do-not-export-me", "userName": "do-not-export-name", "ipRegion": "do-not-export-region"}


class FakeClient:
    def __init__(self, pages, replies=None):
        self.pages = pages
        self.reply_pages = replies or {}
        self.request_count = 0

    def comments(self, target, page, page_size, order):
        self.request_count += 1
        return self.pages[page]

    def replies(self, target, root_id, page, page_size):
        self.request_count += 1
        return self.reply_pages[root_id, page]


def test_short_page_hasnext_and_duplicate_highlights(tmp_path):
    root = comment(1)
    client = FakeClient({1: CommentPage([root], True, [root]), 2: CommentPage([comment(2)], False, [root])})
    result = collect_comments(client, [PostTarget("123")], tmp_path / "run", max_comments=20)
    assert result["total_comments"] == 2
    assert result["status"] == "complete_within_scope"
    raw = (tmp_path / "run/comments.jsonl").read_text(encoding="utf-8")
    assert "do-not-export" not in raw


def test_resume_partial_page_does_not_lose_or_duplicate_rows(tmp_path):
    page = CommentPage([comment(1), comment(2), comment(3)], False, [])
    target = PostTarget("123")
    output = tmp_path / "run"
    first = collect_comments(FakeClient({1: page}), [target], output, max_comments=2)
    assert first["status"] == "limited"
    assert first["posts"]["123"]["stop_reason"] == "comment_limit"
    second = collect_comments(FakeClient({1: page}), [target], output, max_comments=10, resume=True)
    assert second["total_comments"] == 3
    assert second["status"] == "complete_within_scope"


def test_inline_replies_are_not_complete_reply_collection(tmp_path):
    root = comment(1, replies=2)
    root["replyVos"] = [{"replyId": "2", "replyContentStr": "掉帧", "postCommentId": "1"}]
    client = FakeClient({1: CommentPage([root], False, [])}, {
        ("1", 1): CommentPage([{"replyId": "2", "replyContentStr": "掉帧"}, {"replyId": "3", "replyContentStr": "发热"}], False, [])})
    result = collect_comments(client, [PostTarget("123")], tmp_path / "run", max_comments=20)
    assert result["total_comments"] == 3
    assert result["replies"] == 2
    assert result["status"] == "complete_within_scope"


def test_no_replies_scope_drops_previews(tmp_path):
    root = comment(1, replies=1)
    root["replyVos"] = [{"replyId": "2", "replyContentStr": "掉帧"}]
    result = collect_comments(FakeClient({1: CommentPage([root], False, [])}), [PostTarget("123")], tmp_path / "run", include_replies=False)
    assert result["total_comments"] == 1
    assert result["scope"]["include_replies"] is False


def test_repeated_page_stops_and_keeps_partial_work(tmp_path):
    page = CommentPage([comment(1)], True, [])
    out = tmp_path / "run"
    with pytest.raises(SchemaChanged):
        collect_comments(FakeClient({1: page, 2: page}), [PostTarget("123")], out)
    manifest = json.loads((out / "collection_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["total_comments"] == 1


def test_resume_rejects_different_sampling_scope(tmp_path):
    out = tmp_path / "run"
    collect_comments(FakeClient({1: CommentPage([], False, [])}), [PostTarget("123")], out)
    with pytest.raises(ResearchError):
        collect_comments(FakeClient({}), [PostTarget("456")], out, resume=True)


def test_new_run_cannot_overwrite_existing(tmp_path):
    out = tmp_path / "run"
    collect_comments(FakeClient({1: CommentPage([], False, [])}), [PostTarget("123")], out)
    with pytest.raises(ResearchError):
        collect_comments(FakeClient({}), [PostTarget("123")], out)


def test_resume_missing_raw_does_not_create_false_complete_result(tmp_path):
    out = tmp_path / "run"
    collect_comments(FakeClient({1: CommentPage([comment(1)], False, [])}), [PostTarget("123")], out)
    (out / "comments.jsonl").rename(out / "comments.backup.jsonl")
    with pytest.raises(ResearchError, match="comments.jsonl"):
        collect_comments(FakeClient({}), [PostTarget("123")], out, resume=True)
    assert not (out / "comments.jsonl").exists()


def test_resume_truncated_raw_does_not_hide_data_loss(tmp_path):
    out = tmp_path / "run"
    collect_comments(FakeClient({1: CommentPage([comment(1), comment(2)], False, [])}), [PostTarget("123")], out)
    raw = out / "comments.jsonl"
    first_line = raw.read_text(encoding="utf-8").splitlines()[0]
    raw.write_text(first_line + "\n", encoding="utf-8")
    with pytest.raises(ResearchError, match="少于"):
        collect_comments(FakeClient({}), [PostTarget("123")], out, resume=True)
