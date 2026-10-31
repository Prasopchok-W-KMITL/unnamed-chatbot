import asyncio
from datetime import datetime, timezone
from email.utils import format_datetime
import json
import logging

import httpx
from pydantic import ValidationError
import pytest

from app.config import Settings
from app.providers import Providers, ProviderUnavailable
from app.diagnostics import request_trace
from conftest import connected


class Clock:
    def __init__(self): self.elapsed=0.;self.waits=[]
    def __call__(self): return self.elapsed
    async def sleep(self,seconds): self.waits.append(seconds);self.elapsed+=seconds


def setup(settings,kb,store,respond,**updates):
    s=connected(settings).model_copy(update=updates)
    store.settings=s
    clock=Clock()
    client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    provider=Providers(s,kb,store,client,sleep=clock.sleep,clock=clock,wall_clock=lambda:1_800_000_000)
    return provider,client,clock


def good():
    return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})


def usage(store,role='planner'):
    from app.preflight import budget_snapshot
    return budget_snapshot(store.settings)['roles'][role]['day_used']


@pytest.mark.parametrize('status',[429,503])
async def test_retry_once_respects_header_payload_privacy_and_budget(settings,kb,store,status,caplog):
    caplog.set_level(logging.INFO,logger='unnamed.diagnostics')
    calls=[]
    def respond(request):
        calls.append(request)
        return httpx.Response(status,headers={'Retry-After':'1'},json={'private':'private-body'}) if len(calls)==1 else good()
    provider,client,clock=setup(settings,kb,store,respond)
    async with client:
        with request_trace('text'):
            assert await provider.completion('planner',provider.s.openrouter_model,[])=={}
    assert len(calls)==2 and clock.waits==[1.]
    assert calls[0].content==calls[1].content
    assert calls[0].headers['authorization']==calls[1].headers['authorization']
    assert usage(store)==2
    assert json.loads(calls[1].content)['provider']=={'data_collection':'deny','zdr':True,'require_parameters':True,'max_price':{'prompt':0,'completion':0}}
    assert 'retry_attempt' in caplog.text and 'private-body' not in caplog.text
    assert provider.s.openrouter_api_key not in caplog.text


async def test_no_header_uses_bounded_backoff_and_stops_after_two_attempts(settings,kb,store):
    calls=[]
    def respond(request): calls.append(request);return httpx.Response(503)
    provider,client,clock=setup(settings,kb,store,respond)
    async with client:
        with pytest.raises(ProviderUnavailable): await provider.completion('planner',provider.s.openrouter_model,[])
    assert len(calls)==2 and clock.waits==[.5] and usage(store)==2


@pytest.mark.parametrize('status',[400,401,402,403,408,500,502])
async def test_permanent_or_ambiguous_errors_never_retry(settings,kb,store,status):
    calls=[]
    def respond(request): calls.append(request);return httpx.Response(status,headers={'Retry-After':'0'})
    provider,client,clock=setup(settings,kb,store,respond)
    async with client:
        with pytest.raises(ProviderUnavailable): await provider.completion('planner',provider.s.openrouter_model,[])
    assert len(calls)==1 and not clock.waits and usage(store)==1


@pytest.mark.parametrize('header,reason',[('3','retry_wait_too_long'),('9999999999','retry_wait_too_long'),
    ('private-secret-0800000000','invalid_retry_after'),('-1','invalid_retry_after'),('0.5','invalid_retry_after'),
    ('nan','invalid_retry_after'),('x'*129,'invalid_retry_after')])
async def test_unusable_retry_after_skips_without_leaking_header(settings,kb,store,header,reason,caplog):
    caplog.set_level(logging.INFO,logger='unnamed.diagnostics');calls=[]
    def respond(request): calls.append(request);return httpx.Response(429,headers={'Retry-After':header})
    provider,client,clock=setup(settings,kb,store,respond)
    async with client:
        with pytest.raises(ProviderUnavailable): await provider.completion('planner',provider.s.openrouter_model,[])
    assert len(calls)==1 and not clock.waits and usage(store)==1
    assert reason in caplog.text and 'private-secret' not in caplog.text


async def test_http_date_retry_after_is_honored(settings,kb,store):
    calls=[]
    date=format_datetime(datetime.fromtimestamp(1_800_000_001,timezone.utc),usegmt=True)
    def respond(request):
        calls.append(request)
        return httpx.Response(429,headers={'Retry-After':date}) if len(calls)==1 else good()
    provider,client,clock=setup(settings,kb,store,respond,enable_images=True)
    async with client: await provider.completion('vision',provider.s.openrouter_model,[])
    assert len(calls)==2 and clock.waits==[1.]
    assert usage(store,'vision')==2 and usage(store)==0


async def test_attempt_and_wait_share_original_stage_deadline(settings,kb,store,caplog):
    caplog.set_level(logging.INFO,logger='unnamed.diagnostics');calls=[]
    provider=clock=None
    def respond(request):
        calls.append(request);clock.elapsed=7.6
        return httpx.Response(429,headers={'Retry-After':'1'})
    provider,client,clock=setup(settings,kb,store,respond,planner_timeout_seconds=8)
    async with client:
        with pytest.raises(ProviderUnavailable): await provider.completion('planner',provider.s.openrouter_model,[])
    assert len(calls)==1 and not clock.waits and usage(store)==1
    assert 'retry_deadline' in caplog.text


@pytest.mark.parametrize('window',['day','minute'])
async def test_retry_reserves_budget_and_never_bypasses_existing_cap(settings,kb,store,window):
    calls=[]
    def respond(request): calls.append(request);return httpx.Response(429)
    updates={'model_calls_per_day':1} if window=='day' else {'model_calls_per_day':100}
    provider,client,clock=setup(settings,kb,store,respond,**updates)
    if window=='minute':
        for _ in range(14): assert store.reserve_budget('planner')
    async with client:
        with pytest.raises(ProviderUnavailable) as error: await provider.completion('planner',provider.s.openrouter_model,[])
    assert error.value.reason=='budget_exhausted' and len(calls)==1
    assert usage(store)==(1 if window=='day' else 15)


async def test_disable_retries(settings,kb,store):
    calls=[]
    def respond(request): calls.append(request);return httpx.Response(429)
    provider,client,clock=setup(settings,kb,store,respond,provider_max_retries=0)
    async with client:
        with pytest.raises(ProviderUnavailable): await provider.completion('planner',provider.s.openrouter_model,[])
    assert len(calls)==1 and not clock.waits and usage(store)==1


@pytest.mark.parametrize('url',[
    'https://api.line.me/v2/bot/message/reply',
    'https://api.line.me/v2/bot/chat/loading/start',
    'https://api.openai.com/v1/moderations',
    'http://127.0.0.1:9621/query/data',
    'http://127.0.0.1:11434/v1/chat/completions'])
async def test_side_effects_and_guard_retrieval_never_inherit_cloud_retry(settings,kb,store,url):
    calls=[]
    def respond(request): calls.append(request);return httpx.Response(503)
    provider,client,clock=setup(settings,kb,store,respond)
    async with client:
        with pytest.raises(ProviderUnavailable): await provider.json_request('POST',url,retry_role='planner',json={})
    assert len(calls)==1 and not clock.waits and usage(store)==0


@pytest.mark.parametrize('response',[
    {'error':{'code':503,'message':'private-generated-content'}},
    {'choices':[{'message':{'content':'private-invalid-json'}}]},
])
async def test_http_200_model_failures_are_not_replayed(settings,kb,store,response):
    calls=[]
    def respond(request): calls.append(request);return httpx.Response(200,json=response)
    provider,client,clock=setup(settings,kb,store,respond)
    async with client:
        with pytest.raises(ProviderUnavailable): await provider.completion('planner',provider.s.openrouter_model,[])
    assert len(calls)==1 and not clock.waits and usage(store)==1


async def test_hard_total_timeout_and_network_errors_are_not_replayed(settings,kb,store):
    calls=[]
    async def slow(request): calls.append(request);await asyncio.sleep(.1);return good()
    provider,client,clock=setup(settings,kb,store,slow)
    async with client:
        with pytest.raises(ProviderUnavailable) as error:
            await provider.json_request('POST','https://openrouter.ai/api/v1/chat/completions',timeout=.01,retry_role='planner',json={})
    assert error.value.reason=='timeout' and len(calls)==1 and not clock.waits
    calls.clear()
    def fail(request): calls.append(request);raise httpx.ReadTimeout('private-key')
    provider,client,clock=setup(settings,kb,store,fail)
    async with client:
        with pytest.raises(ProviderUnavailable) as error: await provider.completion('planner',provider.s.openrouter_model,[])
    assert error.value.reason=='timeout' and len(calls)==1 and not clock.waits


def test_invalid_retry_settings_are_rejected():
    for kwargs in [{'provider_max_retries':2},{'provider_max_retries':-1},{'provider_retry_max_wait_seconds':0},
                   {'line_loading_seconds':6},{'line_loading_seconds':65}]:
        with pytest.raises(ValidationError): Settings(_env_file=None,**kwargs)
