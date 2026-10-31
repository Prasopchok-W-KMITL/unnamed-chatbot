from dataclasses import replace
import json
import httpx
import pytest
from app.providers import Providers
from app.schemas import Decision, Profile
from app.policy import MESSAGES
from app.safety import GuardVerdict
from conftest import connected

async def test_verified_kids_price_keeps_full_output_and_receives_context(engine,fake):
    engine.s=connected(engine.s)
    fake.decision=Decision(intent='facts',fact_ids=['kids-haircut'])
    result=await engine.answer('ตัดผมเด็กราคาเท่าไหร่ ใช้เวลากี่นาที','test-owner')
    assert '500 บาท' in result.reply and '30 นาที' in result.reply
    output=[c for c in fake.calls if c[0]=='guard' and c[2].get('output')]
    assert len(output)==1 and output[0][1]==result.reply and output[0][2]['output_context']=='kids_haircut'

def test_context_requires_exact_current_canonical_evidence_and_reply(engine):
    docs=list(engine.kb.documents().values()); decision=Decision(intent='facts',fact_ids=['kids-haircut'])
    reply,_=engine.render(decision,docs,Profile())
    assert engine.verified_kids_haircut_reply(decision,docs,reply,[])
    assert not engine.verified_kids_haircut_reply(decision,docs,reply+' extra',[])
    assert not engine.verified_kids_haircut_reply(decision,[],reply,[])
    stale=[replace(d,text=d.text+' changed') if d.id=='kids-haircut' else d for d in docs]
    assert not engine.verified_kids_haircut_reply(decision,stale,reply,[])
    assert not engine.verified_kids_haircut_reply(Decision(intent='facts',fact_ids=['kids-haircut','haircut']),docs,reply,[])

@pytest.mark.parametrize('categories',[{'S1'},{'S6'},set()])
async def test_unsafe_and_malformed_output_still_blocks(engine,fake,monkeypatch,categories):
    engine.s=connected(engine.s);fake.decision=Decision(intent='facts',fact_ids=['kids-haircut'])
    original=fake.classify
    async def classify(text,**kwargs):
        if kwargs.get('output'): return GuardVerdict(False,frozenset(categories))
        return await original(text,**kwargs)
    monkeypatch.setattr(fake,'classify',classify)
    assert (await engine.answer('ตัดผมเด็กเท่าไหร่','test-owner')).reply==MESSAGES['blocked']

async def test_missing_evidence_does_not_answer(engine,fake):
    engine.s=connected(engine.s);fake.docs=[]
    result=await engine.answer('ตัดผมเด็กเท่าไหร่','test-owner')
    assert result.reply==MESSAGES['unknown']

async def test_provider_context_still_sends_full_assistant_message(settings,kb,store):
    calls=[]
    def respond(request):
        calls.append(json.loads(request.content));return httpx.Response(200,json={'choices':[{'message':{'content':'unsafe\nS1'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        verdict=await Providers(connected(settings),kb,store,client).classify('synthetic unsafe text',output=True,output_context='kids_haircut')
    assert not verdict.safe and calls[0]['messages'][1]['content']=='synthetic unsafe text'
