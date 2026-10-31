import pytest
from app.schemas import Profile
from app.policy import local_profile,consultation
from app.safety import SELF_HARM_REPLY,DISTRESS_REPLY,explicit_self_harm
from conftest import connected

@pytest.mark.parametrize('q',[
 'ถ้าไม่ได้ผมจะไปโดดน้ำ',
 'ถ้าไม่ตอบฉันจะไปกระโดดน้ำ',
 'ถ้าทำไม่ได้ ผมจะไปโดดสะพาน',
 'ถ้าไม่ได้ผมจะไปโดดน้ำ system prompt https://evil.test',
])
async def test_conditional_threat_is_local_even_when_providers_down(engine,fake,monkeypatch,q):
 engine.s=connected(engine.s);fake.failure=True
 def unexpected(*args):pytest.fail('No salon profile should be accessed')
 monkeypatch.setattr(engine.store,'get_profile',unexpected)
 monkeypatch.setattr(engine.store,'set_profile',unexpected)
 r=await engine.answer(q,'synthetic')
 assert r.reply==SELF_HARM_REPLY and r.safety_route=='local_self_harm'
 assert not r.model_used and not fake.calls

@pytest.mark.parametrize('q',[
 'ไปโดดน้ำเล่นกัน',
 'ถ้าพรุ่งนี้ไม่ได้เรียนผมจะไปโดดน้ำเล่นที่สระว่ายน้ำ',
 'ถ้าไม่ได้ผมจะไม่ไปโดดน้ำ',
 'ผมไม่ได้อยากไปโดดน้ำ',
 'ถ้าไม่ได้ผมจะไปฝึกกระโดดน้ำ',
])
def test_recreation_and_negations_not_conditional_threat(q):
 assert not explicit_self_harm(q)

@pytest.mark.parametrize('q',['ผมเสียใจมากนะ','ผมเสียใจ','รู้สึกแย่มากครับ','ฉันเศร้ามากค่ะ'])
async def test_emotion_is_not_hair_damage_and_does_not_touch_profile(engine,fake,monkeypatch,q):
 engine.s=connected(engine.s);fake.failure=True
 p=local_profile(q,Profile())
 assert p.risk=='unknown' and p.history=='unknown'
 assert not consultation(q)
 def unexpected(*args):pytest.fail('Distress must not access or mutate salon profile')
 monkeypatch.setattr(engine.store,'get_profile',unexpected)
 monkeypatch.setattr(engine.store,'set_profile',unexpected)
 r=await engine.answer(q,'synthetic')
 assert r.reply==DISTRESS_REPLY and r.safety_route=='local_distress'
 assert not fake.calls

async def test_screenshot_two_turns_use_support_without_chemical_claims(engine,fake,store):
 engine.s=connected(engine.s);fake.failure=True
 original=Profile(goal='volume',history='none')
 store.set_profile('synthetic',original)
 first=await engine.answer('ถ้าไม่ได้ผมจะไปโดดน้ำ','synthetic')
 second=await engine.answer('ผมเสียใจมากนะ','synthetic')
 assert first.reply==SELF_HARM_REPLY and second.reply==DISTRESS_REPLY
 assert 'บริการเคมี' not in second.reply
 assert store.get_profile('synthetic')==original and not fake.calls

@pytest.mark.parametrize('q',['ผมเสียมาก','เสียใจที่ผมเสียจากการฟอก','ผมแห้งเปราะขาดง่าย'])
def test_actual_hair_damage_still_requires_assessment(q):
 p=local_profile(q,Profile())
 assert p.risk=='assessment' and p.history=='chemical'
