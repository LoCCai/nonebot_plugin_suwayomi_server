from unittest.mock import AsyncMock, MagicMock

import pytest

from plugin_pkg.core import SuwayomiCore
from plugin_pkg.store import JsonKvStore
from plugin_pkg.suwayomi.client import SuwayomiClient
from plugin_pkg.suwayomi.models import Chapter, Manga
from tests.fake_ctx import FakeCtx


@pytest.mark.asyncio
@pytest.mark.parametrize(("total", "urls", "paths"), [
    (3,["a","b","c"],["page.jpg","",""]),
    (33,["a"],["page.jpg"]),
])
async def test_download_does_not_send_incomplete_or_capped_file(tmp_path, monkeypatch, total, urls, paths):
    core = SuwayomiCore({},JsonKvStore(tmp_path/'kv.json'),
                        client=SuwayomiClient('http://localhost:4567','none','',''))
    manga = Manga(id=129,title='作品',source_id=1,url='')
    chapter = Chapter(id=519,name='第1话',chapter_number=1,url='')
    monkeypatch.setattr('plugin_pkg.core.resolve_manga',AsyncMock(return_value=(manga,None)))
    monkeypatch.setattr('plugin_pkg.core.get_or_fetch_chapters',AsyncMock(return_value=[chapter]))
    monkeypatch.setattr('plugin_pkg.core.fetch_pages_local',AsyncMock(return_value=(total,urls,paths,tmp_path)))
    pack = MagicMock()
    monkeypatch.setattr('plugin_pkg.core.pack_images',pack)
    monkeypatch.setattr('plugin_pkg.core.schedule_cleanup',MagicMock())
    ctx = FakeCtx(message_str='漫画 下载 129 第1话 pdf')
    await core.download_chapter(ctx,'','')
    pack.assert_not_called()
    assert all(isinstance(message,str) for message in ctx.sent)
    assert '未生成不完整文件' in ctx.sent[-1] and str(total) in ctx.sent[-1]


@pytest.mark.asyncio
async def test_incomplete_push_retains_failure_for_retry(tmp_path, monkeypatch):
    from plugin_pkg.utils.pusher import push_chapter_file
    monkeypatch.setattr('plugin_pkg.utils.pusher.schedule_cleanup',MagicMock())
    fetch = AsyncMock(return_value=(3,['a','b','c'],['page.jpg','',''],tmp_path))
    send = AsyncMock()
    chapter = Chapter(id=519,name='第1话',chapter_number=1,url='')
    assert not await push_chapter_file({},'verification-only','作品',chapter,fetch,send)
    send.assert_not_awaited()
