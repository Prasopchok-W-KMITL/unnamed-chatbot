import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.diagnostics import request_trace
from app.engine import Engine
from app.providers import Providers, ProviderUnavailable
from app.worker import Worker
from conftest import connected


class Clock:
    def __init__(self): self.elapsed=0.
    def __call__(self): return self.elapsed


def mock_pipeline(kb,clock,calls,retrieval_time=20,planner_time=2):
    def handle(request):
        calls.append(request)
        if request.url.path=='/query/data':
            clock.elapsed+=retrieval_time
            return httpx.Response(200,json={'status':'success','data':{'chunks':[
                {'file_path':kb.file_source(d),'content':d.indexed_text} for d in kb.documents().values()]}})
        if request.url.host=='openrouter.ai':
            clock.elapsed+=planner_time
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'intent':'facts','fact_ids':['opening-hours']})}}]})
        clock.elapsed+=4
        return httpx.Response(200,json={'choices':[{'message':{'content':'safe'}}]})
    return httpx.MockTransport(handle)


async def test_retrieval_uses_configured_twenty_seconds_and_keeps_evidence_checks(settings,kb,store):
    s=connected(settings);calls=[];clock=Clock()
    async with httpx.AsyncClient(transport=mock_pipeline(kb,clock,calls,retrieval_time=0)) as client:
        result=await Providers(s,kb,store,client).retrieve('ร้านเปิดกี่โมง')
    assert calls[0].extensions['timeout']['read']==20
    assert 'opening-hours' in {d.id for d in result}
    assert json.loads(calls[0].content)['chunk_top_k']==20


async def test_slow_retrieval_shortens_planner_and_reserves_output_guard(settings,kb,store):
    s=connected(settings).model_copy(update={'planner_timeout_seconds':20})
    calls=[];clock=Clock();clock.elapsed=1 # time already spent showing loading
    async with httpx.AsyncClient(transport=mock_pipeline(kb,clock,calls,planner_time=10)) as client:
        p=Providers(s,kb,store,client,clock=clock)
        with request_trace('api_text') as trace:
            trace.deadline=40
            answer=await Engine(s,kb,store,p).answer('ร้านเปิดกี่โมง','synthetic-owner')
    retrieval=next(r for r in calls if r.url.path=='/query/data')
    planner=next(r for r in calls if r.url.host=='openrouter.ai')
    assert retrieval.extensions['timeout']['read']==20
    assert planner.extensions['timeout']['read']==pytest.approx(10.5)
    guards=[r for r in calls if r.url.host!='openrouter.ai' and r.url.path.endswith('/chat/completions')]
    assert len(guards)==2 and guards[-1].extensions['timeout']['read']==4
    assert answer.evidence_ids==['opening-hours'] and answer.guard_checked
    assert clock.elapsed==39


async def test_fast_retrieval_preserves_twenty_second_planner_cap(settings,kb,store):
    s=connected(settings).model_copy(update={'planner_timeout_seconds':20})
    calls=[];clock=Clock()
    async with httpx.AsyncClient(transport=mock_pipeline(kb,clock,calls,retrieval_time=.4)) as client:
        p=Providers(s,kb,store,client,clock=clock)
        with request_trace('api_text') as trace:
            trace.deadline=40
            await Engine(s,kb,store,p).answer('ร้านเปิดกี่โมง','synthetic-owner')
    assert next(r for r in calls if r.url.host=='openrouter.ai').extensions['timeout']['read']==20


@pytest.mark.parametrize('role,reserve',[('planner',4.5),('retrieval',6.5)])
async def test_no_time_left_stops_before_budget_reservation_and_http(settings,kb,store,role,reserve):
    s=connected(settings);clock=Clock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:pytest.fail('No time remains'))) as client:
        p=Providers(s,kb,store,client,clock=clock)
        with request_trace('text') as trace:
            trace.deadline=reserve
            with pytest.raises(ProviderUnavailable) as error:
                if role=='retrieval': await p.retrieve('ร้านเปิดกี่โมง')
                else: await p.completion('planner',s.openrouter_model,[])
    assert error.value.reason=='timeout'
    with store.db() as db: assert db.execute('SELECT COUNT(*) FROM counters').fetchone()[0]==0


async def test_worker_inherits_aged_job_deadline_including_loading(settings,kb,store,monkeypatch):
    import app.diagnostics as diagnostics
    s=connected(settings).model_copy(update={'planner_timeout_seconds':20})
    clock=Clock();calls=[];monkeypatch.setattr(diagnostics,'request_clock',clock)
    now=store.clock();store.clock=lambda:now
    store.enqueue('test-event','U'+'a'*32,{'kind':'text','text':'ร้านเปิดกี่โมง','reply_token':'fake','loading_user_id':'U'+'a'*32})
    store.clock=lambda:now+28 # 55 - 28 - 5 = 22 seconds available
    class Line:
        replies=[]
        async def loading(self,user): clock.elapsed+=1
        async def reply(self,token,text): self.replies.append(text)
    line=Line()
    async with httpx.AsyncClient(transport=mock_pipeline(kb,clock,calls,retrieval_time=10.5,planner_time=2)) as client:
        p=Providers(s,kb,store,client,clock=clock)
        assert await Worker(store,Engine(s,kb,store,p),line).once()
    retrieval=next(r for r in calls if r.url.path=='/query/data')
    planner=next(r for r in calls if r.url.host=='openrouter.ai')
    assert retrieval.extensions['timeout']['read']==pytest.approx(10.5)
    assert planner.extensions['timeout']['read']==pytest.approx(2)
    assert clock.elapsed==21.5
    assert len(line.replies)==1 and '10.00–20.00' in line.replies[0]


async def test_retrieval_timeout_is_hard_and_does_not_retry(settings,kb,store):
    s=connected(settings).model_copy(update={'retrieval_timeout_seconds':.06});calls=[]
    async def slow(request): calls.append(request);await asyncio.sleep(.15);return httpx.Response(200,json={})
    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        with pytest.raises(ProviderUnavailable) as error:
            await Providers(s,kb,store,client).retrieve('ร้านเปิดกี่โมง')
    assert error.value.reason=='timeout' and len(calls)==1


def test_retrieval_timeout_setting_bounds():
    assert Settings(_env_file=None).retrieval_timeout_seconds==20
    for timeout in [0,26]:
        with pytest.raises(ValidationError): Settings(_env_file=None,retrieval_timeout_seconds=timeout)
