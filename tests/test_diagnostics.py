import asyncio
import json
import logging
import re
import httpx
import pytest

from app.diagnostics import current, request_trace, diagnostic_file, record, logger
from app.engine import Engine
from app.providers import Providers
from app.schemas import Decision
from app.policy import MESSAGES
from conftest import connected


def events(caplog):
    return [json.loads(r.message) for r in caplog.records if r.name=='unnamed.diagnostics']


async def test_trace_has_stage_timings_and_no_customer_or_owner(engine,fake,caplog):
    caplog.set_level(logging.INFO,logger='unnamed.diagnostics')
    engine.s=connected(engine.s)
    fake.decision=Decision(intent='facts',fact_ids=['haircut'])
    result=await engine.answer('ตัดผมราคาเท่าไหร่','private-owner-123')
    rows=events(caplog)
    assert re.fullmatch('[0-9a-f]{32}',result.request_id)
    assert {r['request_id'] for r in rows}=={result.request_id}
    assert rows[0]['event']=='request_started' and rows[-1]['event']=='request_finished'
    assert rows[-1]['outcome']=='facts'
    stages={r['stage'] for r in rows if r['event']=='stage_finished'}
    assert {'guard_input','retrieval','planner','render','guard_output','store_profile'}<=stages
    assert all(r['duration_ms']>=0 for r in rows if 'duration_ms' in r)
    assert 'private-owner' not in caplog.text and 'ตัดผม' not in caplog.text
    assert current.get() is None


async def test_concurrent_requests_have_separate_contexts(engine,caplog):
    caplog.set_level(logging.INFO,logger='unnamed.diagnostics')
    results=await asyncio.gather(engine.answer('สวัสดี','private-owner-a'),engine.answer('เริ่มใหม่','private-owner-b'))
    assert len({r.request_id for r in results})==2
    for result in results:
        rows=[r for r in events(caplog) if r['request_id']==result.request_id]
        assert sum(r['event']=='request_started' for r in rows)==1
        assert sum(r['event']=='request_finished' for r in rows)==1
    assert current.get() is None


async def test_nested_trace_reuses_id_and_local_filter_never_calls_provider(engine,fake,caplog):
    caplog.set_level(logging.INFO,logger='unnamed.diagnostics')
    with request_trace('api_text') as trace:
        answer=await engine.answer('เบอร์ผม 0800000000','private-owner')
        assert answer.request_id==trace.request_id
    rows=events(caplog)
    assert sum(r['event']=='request_finished' for r in rows)==1
    assert any(r.get('reason')=='local_filter' for r in rows)
    assert not fake.calls and '0800000000' not in caplog.text


async def test_guard_failure_records_reason_without_exception_message(engine,fake,caplog):
    caplog.set_level(logging.INFO,logger='unnamed.diagnostics')
    engine.s=connected(engine.s); fake.failure=True
    answer=await engine.answer('ตัดผม','private-owner')
    rows=events(caplog)
    assert answer.reply==MESSAGES['unavailable']
    assert any(r['event']=='failure' and r['stage']=='guard_input' and r['reason']=='internal_error' for r in rows)
    assert rows[-1]['outcome']=='unavailable'
    assert 'private provider error' not in caplog.text


async def test_guard_rejection_records_safe_category_labels(engine,fake,caplog):
    caplog.set_level(logging.INFO,logger='unnamed.diagnostics')
    engine.s=connected(engine.s);fake.safe=False;fake.categories=frozenset({'S7','private-category-0800000000'})
    assert (await engine.answer('ตัดผม','private-owner')).reply==MESSAGES['blocked']
    assert any(r.get('reason')=='guard_unsafe' and r['categories']==['S7'] for r in events(caplog))
    assert 'private-category' not in caplog.text


async def test_http_429_is_attributed_to_guard_request_without_response_body(settings,kb,store,caplog):
    caplog.set_level(logging.INFO,logger='unnamed.diagnostics')
    s=connected(settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(429,json={'error':'private-provider-body'}))) as client:
        result=await Engine(s,kb,store,Providers(s,kb,store,client)).answer('ตัดผม','private-owner')
    rows=events(caplog)
    assert any(r['event']=='provider_error' and r['reason']=='rate_limited' and r['status']==429 for r in rows)
    assert any(r['event']=='failure' and r['stage']=='guard_input' and r['reason']=='rate_limited' for r in rows)
    assert {r['request_id'] for r in rows}=={result.request_id}
    assert 'private-provider-body' not in caplog.text


async def test_invalid_image_failure_preserves_decode_stage(settings,kb,store,caplog):
    caplog.set_level(logging.INFO,logger='unnamed.diagnostics')
    s=connected(settings).model_copy(update={'enable_images':True})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:pytest.fail('Must not call a provider'))) as client:
        answer=await Engine(s,kb,store,Providers(s,kb,store,client)).image(b'private-image-bytes','private-owner')
    assert answer.reply==MESSAGES['unavailable']
    assert any(r['event']=='failure' and r['stage']=='image_decode' and r['reason']=='invalid_image' for r in events(caplog))
    assert 'private-image-bytes' not in caplog.text


def test_file_handler_rotation_cleanup_and_disabled_mode(settings):
    before=list(logger.handlers)
    with diagnostic_file(settings):
        handler=next(h for h in logger.handlers if h not in before)
        handler.maxBytes=300
        with request_trace('text'):
            for i in range(30): record('decision',stage='local_policy',outcome='ok')
    assert logger.handlers==before
    target=settings.data_dir/'runtime'
    files=list(target.glob('diagnostics.jsonl*'))
    assert 1<len(files)<=4
    for f in files:
        for line in f.read_text(encoding='utf-8').splitlines(): json.loads(line)
    disabled=settings.model_copy(update={'data_dir':settings.data_dir/'disabled','diagnostics_enabled':False})
    with diagnostic_file(disabled):
        with request_trace('text'): record('decision',outcome='ok')
    assert not disabled.data_dir.exists()


async def test_invalid_planner_schema_gets_specific_reason(settings,kb,store,caplog):
    caplog.set_level(logging.INFO,logger='unnamed.diagnostics')
    s=connected(settings)
    def respond(request):
        if request.url.path=='/query/data':
            return httpx.Response(200,json={'status':'success','data':{'chunks':[
                {'file_path':kb.file_source(d),'content':d.indexed_text} for d in kb.documents().values()]}})
        if request.url.host=='openrouter.ai':
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'intent':'facts','fact_ids':['haircut'],'private-field':'private-model-value'})}}]})
        return httpx.Response(200,json={'choices':[{'message':{'content':'safe'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        answer=await Engine(s,kb,store,Providers(s,kb,store,client)).answer('ตัดผม','private-owner')
    assert answer.reply==MESSAGES['unavailable']
    assert any(r['event']=='failure' and r['stage']=='planner' and r['reason']=='invalid_schema' for r in events(caplog))
    assert 'private-model' not in caplog.text


async def test_worker_delivery_uses_same_trace_and_cleans_payload(settings,kb,caplog):
    from app.main import create_app
    from fastapi.testclient import TestClient
    from test_api import transport, signed, line_event
    caplog.set_level(logging.INFO,logger='unnamed.diagnostics')
    s,calls=connected(settings),[]
    http=httpx.AsyncClient(transport=transport(kb,s,calls))
    app=create_app(s,client=http,start_worker=False)
    with TestClient(app) as client:
        raw,headers=signed(s,[line_event()])
        assert client.post('/webhook/line',content=raw,headers=headers).status_code==200
        assert await app.state.worker.once()
        logrows=[json.loads(line) for line in (s.data_dir/'runtime/diagnostics.jsonl').read_text(encoding='utf-8').splitlines()]
        assert len({r['request_id'] for r in logrows})==1
        assert all(r['kind']=='line_text' for r in logrows)
        assert any(r.get('stage')=='line_reply' for r in logrows)
        assert any(r.get('stage')=='queue' for r in logrows)
        assert logrows[-1]['outcome']=='facts'
        assert all(r['time'].endswith('+07:00') for r in logrows)
        rawlog=json.dumps(logrows)
        assert 'reply-for-new-bot' not in rawlog and 'U'+'c'*32 not in rawlog
        with app.state.store.db() as db: assert db.execute('SELECT payload FROM jobs').fetchone()[0] is None
    await http.aclose()
