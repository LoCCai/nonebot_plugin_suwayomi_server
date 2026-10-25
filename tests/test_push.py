"""Tests for auto-push message building and delivery (utils.pusher + suwayomi.msg).

移植说明：原 AstrBot 版通过 CompSpy 验证 MessageChain 结构；NoneBot2 版
直接对真实 ``build_image_msg`` / ``push_chapter_images`` 断言 Msg 结构。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import plugin_pkg.utils.pusher as pusher_module
import pytest
from plugin_pkg.suwayomi.msg import Forward, ForwardNode, Image, Text
from plugin_pkg.utils.pusher import (
    build_image_msg,
    cancel_pending_cleanups,
    push_chapter_file,
    push_chapter_images,
    schedule_cleanup,
    schedule_cleanup_file,
    supports_forward,
)
from plugin_pkg.suwayomi.models import Chapter


def _make_chapter(num=3.0) -> Chapter:
    return Chapter(id=7, url="", name=f"第{num}话", chapter_number=num)


def _image_msg(page_urls, local_paths=None, fetch_mode="url", **kw):
    return build_image_msg(
        page_urls,
        local_paths or [],
        fetch_mode,
        send_mode=kw.pop("send_mode", "image"),
        forward_ok=kw.pop("forward_ok", False),
        page_label=kw.pop("page_label", "第 3 话"),
        header=kw.pop("header", "📖「测试标题」第 3 话"),
        total_pages=kw.pop("total_pages", len(page_urls)),
        max_pages=kw.pop("max_pages", 30),
        tail_text=kw.pop("tail_text", "... 还有更多页"),
        **kw,
    )


class TestBuildImageMsg:
    def test_inline_mode_sends_direct_images(self):
        msg = _image_msg([f"http://test/page{i}" for i in range(5)])
        types = [type(s).__name__ for s in msg]
        assert types == ["Text"] + ["Image"] * 5

    def test_forward_mode_sends_nodes(self):
        msg = _image_msg(
            [f"http://test/page{i}" for i in range(3)],
            send_mode="forward",
            forward_ok=True,
        )
        assert len(msg) == 1
        assert isinstance(msg[0], Forward)
        nodes = msg[0].nodes
        assert len(nodes) == 4  # title + 3 pages
        assert all(isinstance(n, ForwardNode) for n in nodes)
        assert isinstance(nodes[0].content[0], Text)

    def test_forward_mode_fallback_on_non_forward_platform(self):
        msg = _image_msg(
            [f"http://test/page{i}" for i in range(3)],
            send_mode="forward",
            forward_ok=False,
        )
        assert all(not isinstance(s, Forward) for s in msg)
        assert [type(s).__name__ for s in msg] == ["Text"] + ["Image"] * 3

    def test_inline_with_header_and_tail(self):
        msg = _image_msg(
            ["http://t/p1", "http://t/p2"],
            header="📖「标题」第 1 话",
            total_pages=4,
            max_pages=2,
            tail_text="... 还有 2 页",
        )
        assert len(msg) == 4  # header + 2 images + tail
        assert isinstance(msg[0], Text)
        assert "标题" in msg[0].text
        assert isinstance(msg[-1], Text)
        assert "还有 2 页" in msg[-1].text

    def test_forward_with_header(self):
        msg = _image_msg(
            ["http://t/p1"],
            send_mode="forward",
            forward_ok=True,
            header="📖「标题」第 1 话",
            header_node_name="「标题」第 1 话",
        )
        assert len(msg) == 1
        nodes = msg[0].nodes
        assert len(nodes) == 2  # header node + 1 page node
        assert nodes[0].name == "「标题」第 1 话"

    def test_forward_without_header(self):
        msg = _image_msg(
            ["http://t/p1"],
            send_mode="forward",
            forward_ok=True,
            header=None,
        )
        nodes = msg[0].nodes
        assert len(nodes) == 1

    def test_forward_ignored_on_non_forward_platform(self):
        msg = _image_msg(
            ["http://t/p1"],
            send_mode="forward",
            forward_ok=False,
            header="h",
        )
        assert all(not isinstance(s, Forward) for s in msg)
        assert len(msg) == 2

    def test_download_mode_uses_local_files(self):
        msg = _image_msg(
            ["http://t/p1"],
            local_paths=["/tmp/p1.jpg"],
            fetch_mode="download",
            header="h",
        )
        images = [s for s in msg if isinstance(s, Image)]
        assert len(images) == 1
        assert images[0].path == "/tmp/p1.jpg"

    def test_download_mode_missing_local_falls_back_to_url(self):
        msg = _image_msg(
            ["http://t/p1"],
            local_paths=[None],
            fetch_mode="download",
        )
        images = [s for s in msg if isinstance(s, Image)]
        assert images[0].url == "http://t/p1"


class TestSupportsForward:
    def test_group_target_true(self):
        assert supports_forward("OneBot V11|123|group:999") is True

    def test_private_target_false(self):
        assert supports_forward("OneBot V11|123|private:999") is False

    def test_invalid_key_false(self):
        assert supports_forward("unknown") is False


class TestPushChapterImages:
    def _client(self):
        client = MagicMock()
        client.auth_mode = "none"
        client.build_image_url = lambda p: f"http://test/{p}"
        return client

    async def _run(self, config, pages, send_fn, chapter=None):
        fetch_fn = AsyncMock(return_value=(len(pages), pages, pages, None))
        return await push_chapter_images(
            self._client(),
            config,
            "OneBot V11|1|group:1",
            "测试标题",
            chapter or _make_chapter(),
            fetch_fn,
            send_fn,
        )

    @pytest.mark.asyncio
    async def test_inline_mode_sends_message(self):
        send_fn = AsyncMock()
        await self._run({"max_pages": 30, "send_mode": "image"}, ["/p1", "/p2"], send_fn)
        send_fn.assert_awaited_once()
        msg = send_fn.call_args[0][1]
        assert any(isinstance(s, Text) and "测试标题" in s.text for s in msg)

    @pytest.mark.asyncio
    async def test_no_pages_skips_sending(self):
        send_fn = AsyncMock()
        await self._run({"max_pages": 30, "send_mode": "image"}, [], send_fn)
        send_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_downloads_failed_skips_sending(self):
        send_fn = AsyncMock()
        fetch_fn = AsyncMock(return_value=(2, ["/p1", "/p2"], [None, None], None))
        await push_chapter_images(
            self._client(),
            {"max_pages": 30, "send_mode": "image", "image_fetch_mode": "download"},
            "OneBot V11|1|group:1",
            "测试标题",
            _make_chapter(),
            fetch_fn,
            send_fn,
        )
        send_fn.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_failure_falls_back_to_text(self):
        send_fn = AsyncMock(side_effect=[RuntimeError("boom"), None])
        await self._run({"max_pages": 30, "send_mode": "image"}, ["/p1"], send_fn)
        assert send_fn.await_count == 2
        fallback = send_fn.call_args[0][1]
        assert any(isinstance(s, Text) and "图片发送失败" in s.text for s in fallback)

    @pytest.mark.asyncio
    async def test_forward_mode_for_group_target(self):
        send_fn = AsyncMock()
        await self._run(
            {"max_pages": 30, "send_mode": "forward"}, ["/p1", "/p2"], send_fn
        )
        msg = send_fn.call_args[0][1]
        assert len(msg) == 1 and isinstance(msg[0], Forward)


class TestPushChapterFile:
    @pytest.mark.asyncio
    async def test_packs_and_sends_file(self, tmp_path, monkeypatch):
        img = tmp_path / "p1.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        send_fn = AsyncMock()
        fetch_fn = AsyncMock(return_value=(1, [str(img)], [str(img)], tmp_path))
        monkeypatch.setattr(
            pusher_module, "pack_images", lambda paths, out, fmt: Path(out).write_bytes(b"x")
        )
        await push_chapter_file(
            {"download_format": "zip"},
            "OneBot V11|1|group:1",
            "测试标题",
            _make_chapter(),
            fetch_fn,
            send_fn,
        )
        send_fn.assert_awaited_once()
        from plugin_pkg.suwayomi.msg import FileSeg

        msg = send_fn.call_args[0][1]
        assert any(isinstance(s, FileSeg) for s in msg)

    @pytest.mark.asyncio
    async def test_no_pages_skips(self):
        send_fn = AsyncMock()
        fetch_fn = AsyncMock(return_value=(0, [], [], None))
        await push_chapter_file(
            {"download_format": "zip"},
            "OneBot V11|1|group:1",
            "测试标题",
            _make_chapter(),
            fetch_fn,
            send_fn,
        )
        send_fn.assert_not_awaited()


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_removes_dir_after_delay(self, tmp_path):
        d = tmp_path / "x"
        d.mkdir()
        schedule_cleanup(d, delay=0.05)
        await asyncio.sleep(0.2)
        assert not d.exists()

    def test_cleanup_skips_none(self):
        assert schedule_cleanup(None) is None

    @pytest.mark.asyncio
    async def test_cancel_pending_cleanups(self, tmp_path):
        d = tmp_path / "y"
        d.mkdir()
        task = schedule_cleanup(d, delay=60)
        assert task is not None
        assert task in pusher_module._cleanup_tasks
        n = cancel_pending_cleanups()
        assert n >= 1
        assert len(pusher_module._cleanup_tasks) == 0

    @pytest.mark.asyncio
    async def test_schedule_cleanup_file_deletes_file(self, tmp_path):
        target = tmp_path / "card.jpg"
        target.write_bytes(b"x")
        schedule_cleanup_file(str(target), delay=0.05)
        await asyncio.sleep(0.2)
        assert not target.exists()

    @pytest.mark.asyncio
    async def test_schedule_cleanup_file_none_noop(self):
        assert schedule_cleanup_file(None) is None
