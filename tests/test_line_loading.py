import asyncio
import json
import logging
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.line import Line
from app.main import create_app
from app.policy import MESSAGES
from app.providers import ProviderUnavailable, Providers
from app.worker import Worker
from conftest import connected
from test_api import line_event, signed, transport

USER = 'U' + 'c' * 32


def loading_settings(settings, **updates):
    return connected(settings).model_copy(update={
        'line_loading_enabled': True, 'line_loading_seconds': 40, **updates})


@pytest.mark.parametrize('seconds', [5, 40, 60])
async def test_loading_uses_new_channel_target_duration_and_202_without_budget(settings,kb,store,seconds):
    s,calls=loading_settings(settings,line_loading_seconds=seconds),[]
    def respond(request):
        calls.append(request)
        return httpx.Response(202, content=b'')
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await Line(s,Providers(s,kb,store,client)).loading(USER)
    assert len(calls)==1 and calls[0].method=='POST'
    assert str(calls[0].url)=='https://api.line.me/v2/bot/chat/loading/start'
    assert calls[0].headers['authorization']=='Bearer new-token'
    assert json.loads(calls[0].content)=={'chatId':USER,'loadingSeconds':seconds}
    assert calls[0].extensions['timeout']['read']==1
    with store.db() as db: assert db.execute('SELECT COUNT(*) FROM counters').fetchone()[0]==0


@pytest.mark.parametrize('target', ['C'+'a'*32, 'R'+'a'*32, 'U'+'a'*31, 'U'+'A'*32, None, 123, 'private-target'])
async def test_loading_invalid_targets_do_not_send(settings,kb,store,target):
    s=loading_settings(settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:pytest.fail('Unexpected request'))) as client:
        with pytest.raises(ProviderUnavailable): await Line(s,Providers(s,kb,store,client)).loading(target)


@pytest.mark.parametrize('seconds', [0, 4, 6, 61, 65, True, '40'])
async def test_loading_invalid_duration_does_not_send(settings,kb,store,seconds):
    s=loading_settings(settings,line_loading_seconds=seconds)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:pytest.fail('Unexpected request'))) as client:
        with pytest.raises(ProviderUnavailable): await Line(s,Providers(s,kb,store,client)).loading(USER)


async def test_loading_disabled_never_sends_or_carries_id(settings,kb,store):
    s=loading_settings(settings,line_loading_enabled=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:pytest.fail('Unexpected request'))) as client:
        line=Line(s,Providers(s,kb,store,client))
        assert await line.loading(USER) is False
        assert 'loading_user_id' not in line.parse_event(line_event())[2]


@pytest.mark.parametrize('status', [200,400,429,503])
async def test_loading_http_failures_never_retry_or_log_body(settings,kb,store,caplog,status):
    s,calls=loading_settings(settings),[]
    def respond(request):
        calls.append(request)
        return httpx.Response(status,json={'private-body':USER})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderUnavailable): await Line(s,Providers(s,kb,store,client)).loading(USER)
    assert len(calls)==1 and USER not in caplog.text and 'private-body' not in caplog.text


def test_only_supported_direct_messages_carry_loading_target(settings,kb,store):
    s=loading_settings(settings,enable_images=True)
    line=Line(s,Providers(s,kb,store,None))
    assert line.parse_event(line_event())[2]['loading_user_id']==USER
    image=line_event(); image['message']={'type':'image','id':'123'}
    assert line.parse_event(image)[2]['loading_user_id']==USER
    group=line_event();group['source']['type']='group'
    assert line.parse_event(group) is None
    other=line_event();other['message']={'type':'video','id':'123'}
    assert 'loading_user_id' not in line.parse_event(other)[2]
    long=line_event(text='x'*1001)
    assert 'loading_user_id' not in line.parse_event(long)[2]
    follow=line_event(); follow['type']='follow';follow.pop('message')
    assert 'loading_user_id' not in line.parse_event(follow)[2]
    noimages=Line(s.model_copy(update={'enable_images':False}),Providers(s,kb,store,None))
    assert 'loading_user_id' not in noimages.parse_event(image)[2]


async def test_ack_before_loading_duplicate_skipped_and_id_only_in_encrypted_queue(settings,kb,caplog):
    caplog.set_level(logging.INFO,logger='unnamed.diagnostics')
    s,calls=loading_settings(settings),[]
    http=httpx.AsyncClient(transport=transport(kb,s,calls))
    app=create_app(s,client=http,start_worker=False)
    with TestClient(app) as client:
        raw,headers=signed(s,[line_event()])
        assert client.post('/webhook/line',content=raw,headers=headers).status_code==200
        assert [r.url.path for r in calls]==['/v2/bot/info']
        with app.state.store.db() as db:
            row=db.execute('SELECT owner,payload FROM jobs').fetchone()
            assert row['owner']!=USER and USER.encode() not in row['payload']
            assert app.state.store.unpack(row['payload'])['loading_user_id']==USER
        assert await app.state.worker.once()
        paths=[r.url.path for r in calls]
        assert paths[1]=='/v2/bot/chat/loading/start'
        assert paths[-1]=='/v2/bot/message/reply'
        for request in calls:
            if request.url.host=='openrouter.ai':
                assert USER.encode() not in request.content and b'loading_user_id' not in request.content
        assert client.post('/webhook/line',content=raw,headers=headers).status_code==200
        assert not await app.state.worker.once()
        assert sum(r.url.path.endswith('/loading/start') for r in calls)==1
        with app.state.store.db() as db:
            row=db.execute('SELECT status,payload FROM jobs').fetchone()
            assert row['status']=='done' and row['payload'] is None
        for file in s.data_dir.glob('bot.sqlite3*'): assert USER.encode() not in file.read_bytes()
        assert USER not in caplog.text and 'new-token' not in caplog.text
    await http.aclose()


@pytest.mark.parametrize('failure', ['http','timeout'])
async def test_loading_failure_nonfatal_reply_still_sent(settings,kb,caplog,failure):
    caplog.set_level(logging.INFO,logger='unnamed.diagnostics')
    s,calls=loading_settings(settings),[]
    base=transport(kb,s,calls)
    def respond(request):
        if request.url.path.endswith('/loading/start'):
            calls.append(request)
            if failure=='timeout': raise httpx.ReadTimeout('private-token-error')
            return httpx.Response(429,json={'error':'private-token-error'})
        return base.handle_request(request)
    http=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    app=create_app(s,client=http,start_worker=False)
    with TestClient(app) as client:
        raw,headers=signed(s,[line_event()])
        assert client.post('/webhook/line',content=raw,headers=headers).status_code==200
        assert await app.state.worker.once()
        replies=[r for r in calls if r.url.path.endswith('/message/reply')]
        assert len(replies)==1 and '700 บาท' in json.loads(replies[0].content)['messages'][0]['text']
        assert sum(r.url.path.endswith('/loading/start') for r in calls)==1
        assert 'loading_unavailable' in caplog.text
        assert 'private-token-error' not in caplog.text and USER not in caplog.text
    await http.aclose()


async def test_loading_hard_timeout_does_not_block_answer(engine,store):
    engine.s=engine.s.model_copy(update={'line_loading_enabled':True})
    class SlowLine:
        def __init__(self): self.replies=[];self.cancelled=False
        async def loading(self,user):
            try: await asyncio.sleep(10)
            finally: self.cancelled=True
        async def reply(self,token,text): self.replies.append(text)
    line=SlowLine()
    store.enqueue('event',USER,{'kind':'text','text':'ตัดผม','reply_token':'token','loading_user_id':USER})
    started=time.perf_counter()
    assert await Worker(store,engine,line).once()
    assert time.perf_counter()-started<3
    assert line.cancelled and len(line.replies)==1 and '700 บาท' in line.replies[0]


async def test_loading_respects_remaining_worker_deadline(engine,store,monkeypatch):
    engine.s=engine.s.model_copy(update={'line_loading_enabled':True})
    class SlowLine:
        def __init__(self): self.replies=[]
        async def loading(self,user): await asyncio.sleep(10)
        async def reply(self,token,text): self.replies.append(text)
    line=SlowLine();now=store.clock();store.clock=lambda:now
    store.enqueue('event',USER,{'kind':'text','text':'ตัดผม','reply_token':'token','loading_user_id':USER})
    store.clock=lambda:now+49.8
    async def fail_answer(*args): pytest.fail('No inference time remains')
    monkeypatch.setattr(engine,'answer',fail_answer)
    started=time.perf_counter()
    assert await Worker(store,engine,line).once()
    assert time.perf_counter()-started<2
    assert line.replies==[MESSAGES['unavailable']]


async def test_rate_and_other_events_never_load(engine,store):
    engine.s=engine.s.model_copy(update={'line_loading_enabled':True})
    class ReplyLine:
        async def loading(self,user): pytest.fail('Rate and unsupported events must not load')
        async def reply(self,token,text): pass
    for i in range(12): store.enqueue(f'event-{i}',USER,{'kind':'media','reply_token':'token'})
    assert store.enqueue('rate-event',USER,{'kind':'text','text':'ตัดผม','reply_token':'token','loading_user_id':USER})=='rate'
    worker=Worker(store,engine,ReplyLine())
    for i in range(13): assert await worker.once()
    assert not await worker.once()


@pytest.mark.parametrize('message', ['ถ้าไม่ได้ผมจะไปโดดน้ำ','ผมเสียใจมากนะ'])
async def test_local_support_does_not_wait_for_loading(engine,store,message):
    engine.s=engine.s.model_copy(update={'line_loading_enabled':True})
    class ReplyLine:
        async def loading(self,user): pytest.fail('Fixed support must not wait for cosmetic API')
        async def reply(self,token,text): pass
    store.enqueue('event',USER,{'kind':'text','text':message,'reply_token':'token','loading_user_id':USER})
    assert await Worker(store,engine,ReplyLine()).once()
