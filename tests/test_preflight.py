import json
import sqlite3
import httpx
import pytest

from app.preflight import budget_snapshot, check_readiness, metadata, CheckFailure
from conftest import connected

RAG = {'LLM_BINDING': 'ollama', 'EMBEDDING_BINDING': 'ollama',
       'LLM_MODEL': 'gemma3:4b', 'EMBEDDING_MODEL': 'bge-m3:latest'}


def ready_settings(settings):
    return connected(settings).model_copy(update={
        'guard_base_url': 'http://127.0.0.1:11434/v1', 'guard_model': 'llama-guard3:1b'})


def responder(s, kb, calls, overrides=None):
    models = [{'name': name} for name in ['gemma3:4b','bge-m3:latest','llama-guard3:1b']]
    values = {
        '/healthz': {'status': 'ok', 'mode': s.bot_mode},
        '/api/status': {'mode': s.bot_mode, 'documents': len(kb.documents()),
                        'services': len(kb.store['services']), 'images_enabled': s.enable_images,
                        'knowledge_revision': kb.revision(), 'diagnostics_enabled': True, 'diagnostics_version': 1},
        '/api/tags': {'models': models}, '/api/ps': {'models': models},
        '/documents/status_counts': {'status_counts': {'processed': len(kb.documents()), 'failed': 0}},
        '/api/v1/key': {'data': {'limit': 1, 'limit_remaining': .97, 'label': 'private-key-label'}},
        '/v2/bot/info': {'userId': s.line_new_bot_user_id, 'displayName': 'private-name'},
        '/v2/bot/channel/webhook/endpoint': {'active': True, 'endpoint': 'https://example.com/webhook/line'},
    }
    values.update(overrides or {})
    def handle(request):
        calls.append(request)
        assert request.method == 'GET', 'Preflight must never perform writes or inference'
        value = values[request.url.path]
        return value if isinstance(value, httpx.Response) else httpx.Response(200, json=value)
    return httpx.MockTransport(handle)


def test_missing_budget_database_is_not_created(settings):
    snapshot = budget_snapshot(settings)
    assert not snapshot['database_exists']
    assert all(r['day_used']==0 for r in snapshot['roles'].values())
    assert not (settings.data_dir/'bot.sqlite3').exists()


def test_budget_snapshot_preserves_counters_and_expired_rows(settings, store):
    now = 1_000_000
    store.clock = lambda: now
    assert store.reserve_budget('planner')
    with store.db() as db:
        db.execute("INSERT INTO counters VALUES ('expired-test',5,1)")
        before = [tuple(r) for r in db.execute('SELECT * FROM counters ORDER BY id')]
    snapshot = budget_snapshot(settings, now=now)
    assert snapshot['roles']['planner']['day_used']==1
    assert snapshot['roles']['planner']['minute_used']==1
    with store.db() as db:
        after = [tuple(r) for r in db.execute('SELECT * FROM counters ORDER BY id')]
    assert before==after


async def test_preflight_reads_only_metadata_and_does_not_initialize_state(settings, kb):
    s, calls = ready_settings(settings), []
    async with httpx.AsyncClient(transport=responder(s,kb,calls)) as client:
        report = await check_readiness(s,client,rag_config=RAG)
    assert report['status']=='checks_passed'
    assert not report['inference_verified'] and not report['line_delivery_verified']
    assert not report['indexed_content_verified']
    assert len(calls)==8
    assert not (s.data_dir/'bot.sqlite3').exists()
    assert 'private-' not in json.dumps(report)
    assert all(secret not in json.dumps(report) for secret in [s.openrouter_api_key,s.line_new_channel_access_token,s.bot_admin_key])


async def test_local_only_never_contacts_cloud(settings,kb):
    s,calls=ready_settings(settings),[]
    async with httpx.AsyncClient(transport=responder(s,kb,calls)) as client:
        report=await check_readiness(s,client,local_only=True,rag_config=RAG)
    assert not report['cloud_metadata_checked']
    assert {r.url.host for r in calls}=={'127.0.0.1'}
    assert report['checks'][-1]['status']=='skipped'


async def test_lightrag_all_count_is_not_an_unfinished_status(settings, kb):
    s, calls = ready_settings(settings), []
    count = len(kb.documents())
    overrides = {'/documents/status_counts': {'status_counts': {
        'processed': count, 'failed': 0, 'pending': 0, 'all': count}}}
    async with httpx.AsyncClient(transport=responder(s, kb, calls, overrides)) as client:
        report = await check_readiness(s, client, rag_config=RAG)
    row = next(r for r in report['checks'] if r['check'] == 'lightrag_documents')
    assert row['status'] == 'pass' and row['unfinished'] == 0


@pytest.mark.parametrize('overrides,check,reason',[
    ({'/documents/status_counts': {'status_counts': {'processed':22,'failed':1}}},'lightrag_documents','documents_not_ready'),
    ({'/documents/status_counts': {'status_counts': {'processed':23,'pending':1}}},'lightrag_documents','documents_not_ready'),
    ({'/documents/status_counts': {'status_counts': {'processed':23,'failed':1,'all':24}}},'lightrag_documents','documents_not_ready'),
    ({'/documents/status_counts': {'status_counts': {'processed':True}}},'lightrag_documents','invalid_response'),
    ({'/api/tags': {'models': []}},'ollama_models','models_missing'),
    ({'/api/status': {'mode':'connected','documents':23}},'backend','backend_restart_required'),
    ({'/api/v1/key': {'data': {'limit':1,'limit_remaining':0}}},'openrouter_key','credit_limit_exhausted'),
    ({'/api/v1/key': {'data': {'limit':1,'limit_remaining':'private-value'}}},'openrouter_key','invalid_response'),
    ({'/v2/bot/channel/webhook/endpoint': {'active':False,'endpoint':'https://example.com/webhook/line'}},'line_webhook','webhook_not_ready'),
    ({'/v2/bot/channel/webhook/endpoint': {'active':True,'endpoint':'https://example.com/'}},'line_webhook','webhook_not_ready'),
])
async def test_preflight_detects_readiness_problems_without_raw_details(settings,kb,overrides,check,reason):
    s,calls=ready_settings(settings),[]
    async with httpx.AsyncClient(transport=responder(s,kb,calls,overrides)) as client:
        report=await check_readiness(s,client,rag_config=RAG)
    assert report['status']=='needs_attention'
    assert any(r['check']==check and r['reason']==reason and r['status']=='fail' for r in report['checks'])
    assert 'private-' not in json.dumps(report)


async def test_wrong_line_identity_stops_webhook_inspection(settings,kb):
    s,calls=ready_settings(settings),[]
    overrides={'/v2/bot/info': {'userId':'U'+'a'*32}}
    async with httpx.AsyncClient(transport=responder(s,kb,calls,overrides)) as client:
        report=await check_readiness(s,client,rag_config=RAG)
    assert not any(r.url.path.endswith('/webhook/endpoint') for r in calls)
    assert any(r['reason']=='wrong_line_account' for r in report['checks'])


async def test_empty_or_unknown_remote_error_body_is_not_exposed(settings,kb):
    s,calls=ready_settings(settings),[]
    overrides={'/documents/status_counts':httpx.Response(403,json={'detail':'private-secret-token'})}
    async with httpx.AsyncClient(transport=responder(s,kb,calls,overrides)) as client:
        report=await check_readiness(s,client,rag_config=RAG)
    assert any(r['reason']=='authentication_failed' for r in report['checks'])
    assert 'private-secret' not in json.dumps(report)


@pytest.mark.parametrize('limit',[None,2])
async def test_key_cap_above_one_dollar_warns_without_changing_it(settings,kb,limit):
    s,calls=ready_settings(settings),[]
    async with httpx.AsyncClient(transport=responder(s,kb,calls,{'/api/v1/key':{'data':{'limit':limit,'limit_remaining':.5}}})) as client:
        report=await check_readiness(s,client,rag_config=RAG)
    assert any(r['reason']=='key_limit_above_project_budget' and r['status']=='warn' for r in report['checks'])
    assert all(r.method=='GET' for r in calls)


async def test_budget_exhaustion_is_read_only_and_visible(settings,kb,store):
    s=ready_settings(settings).model_copy(update={'model_calls_per_day':1})
    assert store.reserve_budget('planner')
    with store.db() as db: before=[tuple(r) for r in db.execute('SELECT * FROM counters ORDER BY id')]
    async with httpx.AsyncClient(transport=responder(s,kb,[])) as client:
        report=await check_readiness(s,client,rag_config=RAG)
    assert any(r['check']=='local_budgets' and r['status']=='fail' for r in report['checks'])
    with store.db() as db: assert before==[tuple(r) for r in db.execute('SELECT * FROM counters ORDER BY id')]


async def test_metadata_timeout_and_oversized_response_fail_without_contents():
    def timeout(request): raise httpx.ReadTimeout('private-url-and-key')
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(CheckFailure) as error: await metadata(client,'http://127.0.0.1/healthz')
        assert error.value.reason=='timeout'
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'x'*250001))) as client:
        with pytest.raises(CheckFailure) as error: await metadata(client,'http://127.0.0.1/healthz')
        assert error.value.reason=='invalid_response'


def test_cli_configuration_failure_never_echoes_secret(monkeypatch,capsys):
    import app.preflight as preflight
    def invalid(): raise ValueError('private-config-key-value')
    monkeypatch.setattr(preflight,'Settings',invalid)
    monkeypatch.setattr('sys.argv',['preflight','--local-only','--json'])
    with pytest.raises(SystemExit) as error: preflight.main()
    assert error.value.code==1
    output=capsys.readouterr().out
    assert 'private-config' not in output
    assert json.loads(output)['checks'][0]['reason']=='configuration_or_check_failed'
