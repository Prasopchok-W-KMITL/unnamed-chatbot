import io
import json
import httpx
import pytest
from PIL import Image
from pydantic import ValidationError
from app.config import Settings
from app.engine import Engine
from app.policy import MESSAGES
from app.providers import Providers, ProviderUnavailable
from conftest import connected


CATEGORY_NAMES = ('harassment', 'harassment/threatening', 'hate', 'hate/threatening',
    'illicit', 'illicit/violent', 'self-harm', 'self-harm/intent', 'self-harm/instructions',
    'sexual', 'sexual/minors', 'violence', 'violence/graphic')


def moderation_result(flagged=False, category=None):
    categories = dict.fromkeys(CATEGORY_NAMES, False)
    if category: categories[category] = True
    return {'model': 'omni-moderation-latest', 'results': [
        {'flagged': flagged, 'categories': categories}
    ]}


def image_settings(settings, **updates):
    return connected(settings).model_copy(update={
        'enable_images': True, 'image_guard_provider': 'openai',
        'image_guard_model': 'omni-moderation-latest',
        'openai_moderation_api_key': 'synthetic-openai-key',
        'guard_api_key': 'synthetic-local-guard-key',
        'openrouter_vision_model': 'vision/model:free', **updates})


def image_bytes():
    output = io.BytesIO()
    Image.new('RGB', (20, 20), 'white').save(output, 'PNG')
    return output.getvalue()


async def test_openai_image_guard_runs_before_vision_and_keeps_keys_separate(settings, kb, store):
    s, calls = image_settings(settings), []
    def respond(request):
        calls.append(request)
        if request.url.host == 'api.openai.com':
            return httpx.Response(200, json=moderation_result())
        if request.url.host == 'openrouter.ai':
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
                'kind': 'hairstyle', 'hair_length': 'short', 'texture': 'wavy', 'volume': 'high'})}}]})
        return httpx.Response(200, json={'choices': [{'message': {'content': 'safe'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        p = Providers(s, kb, store, client)
        assert (await p.describe_image(image_bytes())).texture == 'wavy'
        # Text still uses the original local guard, despite selecting OpenAI for images.
        assert await p.moderate('ตัดผมราคาเท่าไหร่')
    assert [r.url.host for r in calls] == ['api.openai.com', 'openrouter.ai', '127.0.0.1']
    moderation, vision, text = calls
    assert moderation.url.path == '/v1/moderations'
    assert moderation.headers['authorization'] == 'Bearer synthetic-openai-key'
    assert vision.headers['authorization'] == 'Bearer new-router-key'
    assert text.headers['authorization'] == 'Bearer synthetic-local-guard-key'
    payload = json.loads(moderation.content)
    assert payload['model'] == 'omni-moderation-latest'
    assert len(payload['input']) == 1 and payload['input'][0]['type'] == 'image_url'
    url = payload['input'][0]['image_url']['url']
    assert url.startswith('data:image/jpeg;base64,')
    assert json.loads(vision.content)['messages'][1]['content'][1]['image_url']['url'] == url
    assert 'synthetic-openai-key' not in vision.content.decode() + text.content.decode()
    assert not any(settings.data_dir.glob('*.png'))


@pytest.mark.parametrize('flagged,category', [(True, None), (True, 'violence'),
    (False, 'sexual'), (False, 'future-hazard')])
async def test_flagged_or_any_true_category_blocks_before_vision(settings, kb, store, flagged, category):
    calls = []
    def respond(request):
        calls.append(request)
        assert request.url.host == 'api.openai.com'
        return httpx.Response(200, json=moderation_result(flagged, category))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await Providers(image_settings(settings), kb, store, client).describe_image(image_bytes()) is None
    assert len(calls) == 1


@pytest.mark.parametrize('case', ['empty-results', 'multiple-results', 'missing-results',
    'missing-flagged', 'string-flagged', 'missing-category', 'string-category', 'numeric-category'])
async def test_malformed_moderation_response_never_allows_vision(settings, kb, store, case):
    payload = moderation_result()
    row = payload['results'][0]
    if case == 'empty-results': payload['results'] = []
    elif case == 'multiple-results': payload['results'] *= 2
    elif case == 'missing-results': payload = {}
    elif case == 'missing-flagged': del row['flagged']
    elif case == 'string-flagged': row['flagged'] = 'false'
    elif case == 'missing-category': del row['categories']['violence']
    elif case == 'string-category': row['categories']['sexual'] = 'false'
    elif case == 'numeric-category': row['categories']['sexual'] = 0
    calls = []
    def respond(request):
        calls.append(request)
        assert request.url.host == 'api.openai.com'
        return httpx.Response(200, json=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderUnavailable):
            await Providers(image_settings(settings), kb, store, client).describe_image(image_bytes())
    assert len(calls) == 1


@pytest.mark.parametrize('case', ['unauthorized', 'rate-limit', 'server-error', 'redirect', 'timeout', 'invalid-json'])
async def test_openai_errors_stop_image_pipeline_without_logging_secrets(settings, kb, store, caplog, case):
    calls = []
    def respond(request):
        calls.append(request)
        assert request.url.host == 'api.openai.com'
        if case == 'timeout': raise httpx.ReadTimeout('private-error-data')
        status = {'unauthorized': 401, 'rate-limit': 429, 'server-error': 500, 'redirect': 302, 'invalid-json': 200}[case]
        return httpx.Response(status, content=b'private-error-data')
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderUnavailable):
            await Providers(image_settings(settings), kb, store, client).describe_image(image_bytes())
    assert len(calls) == 1
    assert 'private-error-data' not in caplog.text and 'synthetic-openai-key' not in caplog.text
    assert 'data:image' not in caplog.text


@pytest.mark.parametrize('case', ['missing-key', 'wrong-provider-key', 'budget', 'remote-url'])
async def test_unready_image_guard_never_calls_any_service(settings, kb, store, case):
    s = image_settings(settings)
    if case == 'missing-key': s.openai_moderation_api_key = ''
    if case == 'wrong-provider-key': s.openai_moderation_api_key = 'sk-or-synthetic-key'
    if case == 'budget':
        store.settings.guard_calls_per_day = 1
        assert store.reserve_budget('image_guard')
    def unexpected(request): pytest.fail('Unready image guard must not call a provider')
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        p = Providers(s, kb, store, client)
        with pytest.raises(ProviderUnavailable):
            if case == 'remote-url': await p.moderate_image_openai('https://example.com/image.png')
            else: await p.describe_image(image_bytes())


def test_image_config_requires_dedicated_key_and_correct_moderation_model(settings):
    config = image_settings(settings).model_dump()
    assert Settings(_env_file=None, **config).image_guard_provider == 'openai'
    for update in [{'openai_moderation_api_key': ''}, {'openai_moderation_api_key': 'sk-or-synthetic-key'},
                   {'image_guard_model': 'ordinary-chat-model'},
                   {'image_guard_provider': 'unknown'}]:
        with pytest.raises(ValidationError): Settings(_env_file=None, **(config | update))
    # Waiting for a key is allowed while image intake is disabled.
    assert Settings(_env_file=None, **(config | {'enable_images': False, 'openai_moderation_api_key': ''}))


async def test_disabled_images_stay_disabled_while_waiting_for_key(settings, kb, store):
    s = image_settings(settings, enable_images=False, openai_moderation_api_key='')
    def unexpected(request): pytest.fail('Disabled images must not call a provider')
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        result = await Engine(s, kb, store, Providers(s, kb, store, client)).image(image_bytes(), 'synthetic-owner')
    assert result.reply == MESSAGES['media'] and not result.guard_checked and not result.model_used
