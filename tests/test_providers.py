import io
import json
import httpx
import pytest
from PIL import Image
from pydantic import ValidationError
from app.config import Settings
from app.providers import Providers, ProviderUnavailable
from app.schemas import Profile, VisionSummary
from conftest import connected


@pytest.mark.parametrize('reply,expected', [('safe', True), ('unsafe\nS7', False), ('safe because...', False), ('SAFE', False)])
async def test_guard_requires_exact_safe(settings, kb, store, reply, expected):
    s = connected(settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'choices': [{'message': {'content': reply}}]}))) as client:
        assert await Providers(s, kb, store, client).moderate('คำถามร้าน') is expected


async def test_retrieval_uses_only_current_full_original_chunks(settings, kb, store):
    s = connected(settings)
    doc = kb.documents()['haircut']
    chunks = [
        {'file_path': kb.file_source(doc), 'content': doc.indexed_text},
        {'file_path': 'old/haircut.txt', 'content': doc.indexed_text},
        {'file_path': kb.file_source(doc), 'content': doc.indexed_text.replace('700', '1')},
        {'file_path': kb.file_source(doc), 'content': 'UNNAMED_EVIDENCE:secret:fake'},
    ]
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'status': 'success', 'data': {'chunks': chunks}, 'response': 'fake answer 1 baht'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = Providers(s, kb, store, client)
        result = await provider.retrieve('ตัดผม')
        assert [d.id for d in result] == ['haircut']
        assert calls[0].headers['x-api-key'] == 'new-rag-key'
        assert json.loads(calls[0].content)['mode'] == 'mix'
        kb.store['services'][0]['priceBaht'] = 750
        assert await provider.retrieve('ตัดผม') == []


@pytest.mark.parametrize('source,content_kind,accepted', [
    ('haircut.txt', 'current', True),
    ('haircut.txt', 'changed_price', False),
    ('haircut.txt', 'old_hash', False),
    ('haircut.txt', 'fragment', False),
    ('old/haircut.txt', 'current', False),
    ('../haircut.txt', 'current', False),
    ('unknown.txt', 'current', False),
])
async def test_normalized_source_still_requires_current_full_evidence(settings, kb, store, source, content_kind, accepted):
    doc = kb.documents()['haircut']
    content = {
        'current': doc.indexed_text,
        'changed_price': doc.indexed_text.replace('700', '1'),
        'old_hash': doc.indexed_text.replace(doc.marker, 'UNNAMED_EVIDENCE:haircut:old'),
        'fragment': doc.marker,
    }[content_kind]
    payload = {'status': 'success', 'data': {'chunks': [{'file_path': source, 'content': content}]}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as client:
        result = await Providers(connected(settings), kb, store, client).retrieve('ตัดผม')
        assert [d.id for d in result] == (['haircut'] if accepted else [])


async def test_planner_schema_privacy_and_invalid_output(settings, kb, store):
    s, calls = connected(settings), []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({'intent': 'facts', 'fact_ids': ['haircut'], 'secret': 'leak'})}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        p = Providers(s, kb, store, client)
        with pytest.raises(ValidationError): await p.decide('ตัดผม', Profile(), list(kb.documents().values()))
    payload = json.loads(calls[0].content)
    assert payload['provider'] == {'data_collection': 'deny', 'zdr': True, 'require_parameters': True, 'max_price': {'prompt': 0, 'completion': 0}}
    schema = payload['response_format']['json_schema']['schema']
    assert schema['additionalProperties'] is False and set(schema['required']) == set(schema['properties'])


async def test_planner_can_disable_zdr_without_relaxing_other_controls(settings, kb, store):
    s = connected(settings).model_copy(update={'openrouter_zdr': False,
        'openrouter_disable_reasoning': True, 'planner_timeout_seconds': 20})
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'intent': 'facts', 'fact_ids': ['haircut']})}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        decision = await Providers(s, kb, store, client).decide('ตัดผม', Profile(), [kb.documents()['haircut']])
    assert decision.fact_ids == ['haircut']
    payload = json.loads(calls[0].content)
    assert payload['provider'] == {'data_collection': 'deny', 'zdr': False,
        'require_parameters': True, 'max_price': {'prompt': 0, 'completion': 0}}
    assert payload['response_format']['json_schema']['strict'] is True
    assert payload['reasoning'] == {'enabled': False}
    assert calls[0].extensions['timeout']['read'] == 20


@pytest.mark.parametrize('send_temperature', [True, False])
async def test_temperature_can_be_omitted_without_relaxing_validation(settings, kb, store, send_temperature):
    s = connected(settings).model_copy(update={'openrouter_send_temperature': send_temperature,
        'openrouter_disable_reasoning': True})
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'intent': 'facts', 'fact_ids': ['haircut'], 'answer': 'invented'})}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValidationError):
            await Providers(s, kb, store, client).decide('ตัดผม', Profile(), [kb.documents()['haircut']])
    payload = json.loads(calls[0].content)
    assert ('temperature' in payload) is send_temperature
    if send_temperature: assert payload['temperature'] == 0
    assert payload['reasoning'] == {'enabled': False}
    assert payload['provider']['require_parameters'] is True
    assert payload['provider']['data_collection'] == 'deny'
    assert payload['provider']['max_price'] == {'prompt': 0, 'completion': 0}
    assert payload['response_format']['json_schema']['strict'] is True


@pytest.mark.parametrize('value,accepted', [
    ({'intent': 'facts', 'fact_ids': ['haircut']}, True),
    ({'intent': 'facts', 'fact_ids': ['haircut'], 'answer': 'invented price'}, False),
    ({'intent': 'invented-intent'}, False),
    ({'intent': 'facts', 'fact_ids': 'haircut'}, False),
    ([], False),
])
async def test_json_object_planner_still_validates_schema_and_preserves_privacy(settings, kb, store, value, accepted):
    s = connected(settings).model_copy(update={'openrouter_planner_format': 'json_object'})
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(value)}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        p = Providers(s, kb, store, client)
        if accepted:
            assert (await p.decide('ตัดผม', Profile(), [kb.documents()['haircut']])).fact_ids == ['haircut']
        else:
            with pytest.raises(ValidationError):
                await p.decide('ตัดผม', Profile(), [kb.documents()['haircut']])
    payload = json.loads(calls[0].content)
    assert payload['response_format'] == {'type': 'json_object'}
    assert payload['provider'] == {'data_collection': 'deny', 'zdr': True,
        'require_parameters': True, 'max_price': {'prompt': 0, 'completion': 0}}
    schema = json.loads(payload['messages'][0]['content'].split('schema: ', 1)[1])
    assert schema['additionalProperties'] is False
    assert set(schema['required']) == set(schema['properties'])


async def test_json_object_planner_does_not_relax_vision_format(settings, kb, store):
    s = connected(settings).model_copy(update={'openrouter_planner_format': 'json_object'})
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await Providers(s, kb, store, client).completion('vision', 'vision:free', [], schema=VisionSummary.model_json_schema())
    assert json.loads(calls[0].content)['response_format']['type'] == 'json_schema'


@pytest.mark.parametrize('content', ['not-json', '```json\n{}\n```', '{"intent":'])
async def test_json_object_planner_rejects_malformed_json_without_retry(settings, kb, store, content):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})
    s = connected(settings).model_copy(update={'openrouter_planner_format': 'json_object'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderUnavailable):
            await Providers(s, kb, store, client).decide('ตัดผม', Profile(), [kb.documents()['haircut']])
    assert len(calls) == 1


@pytest.mark.parametrize('status,body', [(429, b'private-error'), (302, b''), (200, b'not-json'), (200, b'x' * 250001)], ids=['rate-limit', 'redirect', 'invalid-json', 'oversized'])
async def test_provider_errors_are_fixed_and_bounded(settings, kb, store, status, body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, content=body))) as client:
        with pytest.raises(ProviderUnavailable) as e:
            await Providers(connected(settings), kb, store, client).moderate('text')
        assert 'private' not in str(e.value)


async def test_http_200_provider_error_envelope_is_rejected_without_leaking(settings, kb, store, caplog):
    payload = {'error': {'code': 503, 'message': 'private-provider-error',
        'metadata': {'raw': 'secret-key-and-customer-text'}}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as client:
        with pytest.raises(ProviderUnavailable):
            await Providers(connected(settings), kb, store, client).moderate('private-question')
    assert 'provider_error_envelope code=503' in caplog.text
    assert 'private-' not in caplog.text and 'secret-key' not in caplog.text


def test_real_image_decode_limits_metadata_and_no_disk(settings, kb, store):
    p = Providers(settings, kb, store, None)
    image = Image.new('RGB', (1400, 800), 'white')
    output = io.BytesIO(); image.save(output, 'PNG')
    value = p.image_data_url(output.getvalue())
    assert value.startswith('data:image/jpeg;base64,')
    with pytest.raises(ProviderUnavailable): p.image_data_url(b'fake.png')
    with pytest.raises(ProviderUnavailable): p.image_data_url(b'\x89PNG\r\n\x1a\nnot-valid')
    settings.max_image_bytes = 1024
    with pytest.raises(ProviderUnavailable): p.image_data_url(output.getvalue())
    assert not list(settings.data_dir.glob('*.png'))


async def test_vision_schema_no_free_text_and_guard_before_model(settings, kb, store):
    s = connected(settings).model_copy(update={'enable_images': True, 'image_guard_model': 'vision-guard', 'openrouter_vision_model': 'vision/model:free'})
    calls = []
    def respond(request):
        calls.append(request)
        content = json.dumps({'kind': 'hairstyle', 'hair_length': 'short', 'texture': 'wavy', 'volume': 'high'}) if request.url.host == 'openrouter.ai' else 'safe'
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})
    image = io.BytesIO(); Image.new('RGB', (20, 20)).save(image, 'JPEG')
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        summary = await Providers(s, kb, store, client).describe_image(image.getvalue())
    assert summary.texture == 'wavy' and len(calls) == 2
    assert calls[0].url.host == '127.0.0.1' and calls[1].url.host == 'openrouter.ai'
    assert json.loads(calls[0].content)['model'] == 'vision-guard'


def test_config_refuses_incomplete_connected_paid_and_unprotected_images(settings):
    with pytest.raises(ValidationError): Settings(_env_file=None, openrouter_planner_format='text')
    with pytest.raises(ValidationError): Settings(_env_file=None, bot_mode='connected')
    with pytest.raises(ValidationError): Settings(_env_file=None, openrouter_model='paid/model')
    with pytest.raises(ValidationError): Settings(_env_file=None, enable_images=True)
    with pytest.raises(ValidationError): Settings(_env_file=None, lightrag_mode='bypass')
    with pytest.raises(ValidationError): Settings(_env_file=None, guard_base_url='http://remote.test/v1')
    with pytest.raises(ValidationError): Settings(_env_file=None, planner_timeout_seconds=21)
    with pytest.raises(ValidationError): Settings(_env_file=None, planner_timeout_seconds=0)
