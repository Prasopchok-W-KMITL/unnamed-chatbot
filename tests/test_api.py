import asyncio
import base64
import hashlib
import hmac
import json
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.policy import MESSAGES
from conftest import connected


def line_event(id='event-one', text='ตัดผมราคาเท่าไหร่'):
    return {'webhookEventId': id, 'timestamp': int(time.time() * 1000), 'replyToken': 'reply-for-new-bot',
            'type': 'message', 'source': {'type': 'user', 'userId': 'U' + 'c' * 32},
            'message': {'type': 'text', 'text': text}}


def signed(s, events, destination=None):
    raw = json.dumps({'destination': destination or s.line_new_bot_user_id, 'events': events}, ensure_ascii=False).encode()
    signature = base64.b64encode(hmac.new(s.line_new_channel_secret.encode(), raw, hashlib.sha256).digest()).decode()
    return raw, {'content-type': 'application/json', 'x-line-signature': signature}


def transport(kb, s, calls, fail_reply=False):
    def handle(request):
        calls.append(request)
        path = request.url.path
        if path.endswith('/bot/info'): return httpx.Response(200, json={'userId': s.line_new_bot_user_id})
        if path.endswith('/chat/loading/start'): return httpx.Response(202, json={})
        if path == '/query/data':
            return httpx.Response(200, json={'status': 'success', 'data': {'chunks': [
                {'file_path': kb.file_source(d), 'content': d.indexed_text} for d in kb.documents().values()]}})
        if request.url.host == 'openrouter.ai':
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({'intent': 'facts', 'fact_ids': ['haircut']})}}]})
        if path.endswith('/chat/completions'): return httpx.Response(200, json={'choices': [{'message': {'content': 'safe'}}]})
        if path.endswith('/message/reply'): return httpx.Response(500 if fail_reply else 200, json={})
        raise AssertionError(f'Unexpected endpoint {request.url.host}{path}')
    return httpx.MockTransport(handle)


def test_demo_api_auth_metadata_validation_and_session(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get('/healthz').json() == {'status': 'ok', 'mode': 'demo'}
        assert client.get('/docs').status_code == 404
        assert client.post('/api/chat', json={'message': 'hello'}).status_code == 401
        headers = {'x-admin-key': settings.bot_admin_key}
        result = client.post('/api/chat', headers=headers, json={'message': 'ตัดผมราคาเท่าไหร่'})
        assert result.status_code == 200
        body = result.json()
        assert '700 บาท' in body['reply'] and not body['model_used'] and body['mode'] == 'demo'
        assert body['session_id'] and result.headers['cache-control'] == 'no-store'
        invalid = client.post('/api/chat', headers=headers, json={'message': 'private', 'evil': 'secret'})
        assert invalid.status_code == 422 and 'private' not in invalid.text and 'secret' not in invalid.text
        assert client.post('/api/chat', headers=headers, json={'message': 'x' * 1001}).status_code == 422
        assert client.post('/api/chat', content=b'x' * 9000).status_code == 413
        assert client.post('/webhook/line', json={}).status_code == 503


def test_startup_configuration_error_does_not_echo_values(monkeypatch):
    import app.main as main_module
    def invalid_settings():
        raise ValueError('private-token-value')
    monkeypatch.setattr(main_module, 'Settings', invalid_settings)
    with pytest.raises(RuntimeError) as error:
        create_app()
    assert 'private-token-value' not in str(error.value)
    assert error.value.__suppress_context__ is True


@pytest.mark.parametrize('planner_format', ['json_schema', 'json_object'])
async def test_signed_end_to_end_ack_queue_and_new_token(settings, kb, planner_format):
    s, calls = connected(settings).model_copy(update={'openrouter_planner_format': planner_format}), []
    http = httpx.AsyncClient(transport=transport(kb, s, calls))
    app = create_app(s, client=http, start_worker=False)
    raw, headers = signed(s, [line_event()])
    with TestClient(app) as client:
        assert client.post('/webhook/line', content=raw, headers=headers).status_code == 200
        assert [r.url.path for r in calls] == ['/v2/bot/info']  # ACK before retrieval/model
        assert await app.state.worker.once()
        reply = next(r for r in calls if r.url.path.endswith('/message/reply'))
        assert reply.headers['authorization'] == 'Bearer new-token'
        assert '700 บาท' in json.loads(reply.content)['messages'][0]['text']
        for req in calls:
            if req.url.host == 'openrouter.ai':
                assert b'reply-for-new-bot' not in req.content and ('U' + 'c' * 32).encode() not in req.content
        assert client.post('/webhook/line', content=raw, headers=headers).status_code == 200
        assert not await app.state.worker.once()
    await http.aclose()


async def test_signed_line_self_harm_reply_never_calls_ai_or_retrieval(settings, kb):
    from app.safety import SELF_HARM_REPLY
    s, calls = connected(settings), []
    http = httpx.AsyncClient(transport=transport(kb, s, calls))
    app = create_app(s, client=http, start_worker=False)
    question = 'รู้สึกไม่ดีเลยอยากจบชีวิตตัวเอง'
    with TestClient(app) as client:
        raw, headers = signed(s, [line_event(text=question)])
        assert client.post('/webhook/line', content=raw, headers=headers).status_code == 200
        assert await app.state.worker.once()
        assert [r.url.path for r in calls] == ['/v2/bot/info', '/v2/bot/message/reply']
        assert json.loads(calls[-1].content)['messages'][0]['text'] == SELF_HARM_REPLY
        with app.state.store.db() as db:
            row = db.execute('SELECT status, payload FROM jobs').fetchone()
            assert row['status'] == 'done' and row['payload'] is None
            assert db.execute('SELECT COUNT(*) FROM profiles').fetchone()[0] == 0
    await http.aclose()


def test_bad_signature_wrong_destination_and_json_before_queue(settings, kb):
    s, calls = connected(settings), []
    http = httpx.AsyncClient(transport=transport(kb, s, calls))
    with TestClient(create_app(s, client=http, start_worker=False)) as client:
        raw, headers = signed(s, [line_event()])
        assert client.post('/webhook/line', content=raw + b' ', headers=headers).status_code == 403
        bad, good_headers = signed(s, [], destination='U' + 'd' * 32)
        assert client.post('/webhook/line', content=bad, headers=good_headers).status_code == 403
        assert calls == []
        assert client.post('/webhook/line', content=b'x' * 70000, headers=headers).status_code == 413
        too_many, hs = signed(s, [line_event(str(i)) for i in range(11)])
        assert client.post('/webhook/line', content=too_many, headers=hs).status_code == 400


def test_token_identity_mismatch_fail_closed(settings):
    s = connected(settings)
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'userId': 'U' + 'd' * 32})))
    with TestClient(create_app(s, client=http, start_worker=False)) as client:
        raw, headers = signed(s, [])
        assert client.post('/webhook/line', content=raw, headers=headers).status_code == 503


async def test_uncertain_delivery_does_not_retry(settings, kb):
    s, calls = connected(settings), []
    http = httpx.AsyncClient(transport=transport(kb, s, calls, fail_reply=True))
    app = create_app(s, client=http, start_worker=False)
    with TestClient(app) as client:
        raw, headers = signed(s, [line_event()])
        client.post('/webhook/line', content=raw, headers=headers)
        await app.state.worker.once()
        client.post('/webhook/line', content=raw, headers=headers)
        assert not await app.state.worker.once()
        assert sum(r.url.path.endswith('/message/reply') for r in calls) == 1
    await http.aclose()


def test_line_skips_groups_stale_and_dummy_tokens(settings, kb, store):
    from app.line import Line
    from app.providers import Providers
    s = connected(settings)
    line = Line(s, Providers(s, kb, store, None))
    event = line_event()
    assert line.parse_event(event)
    event['source']['type'] = 'group'
    assert line.parse_event(event) is None
    event = line_event(); event['timestamp'] -= 121000
    assert line.parse_event(event) is None
    event = line_event(); event['replyToken'] = '0' * 32
    assert line.parse_event(event) is None
    event = line_event(); event['webhookEventId'] = 123
    assert line.parse_event(event) is None
    event = line_event(); event['message'] = {'type': 'image', 'id': 123}
    assert line.parse_event(event)[2]['kind'] == 'media'
    assert not line.signature_valid(b'x', 'é' * 44)
