import pytest
from app.policy import local_profile, MESSAGES
from app.schemas import Profile, Decision
from conftest import connected

@pytest.mark.parametrize('text',[
    'ไม่เคยทำเคมี และผมไม่แห้งหรือเปราะขาดง่าย',
    'ไม่เคยทำเคมี ผมไม่เปราะและไม่ขาดง่าย',
    'ไม่เคยทำเคมี ไม่เปราะขาดง่าย',
])
def test_negated_symptoms_do_not_mark_damage(text):
    p=local_profile(text,Profile())
    assert p.history=='none' and p.risk=='unknown'

@pytest.mark.parametrize('text',[
    'ไม่เคยทำเคมี แต่ผมเปราะขาดง่าย',
    'ไม่แห้งแต่เปราะขาดง่าย',
    'ไม่เปราะ แต่ผมเสีย',
    'ไม่เคยฟอก แต่เคยยืด',
    'ไม่เคยทำสี แต่ฟอกมา',
])
def test_real_symptoms_and_treatment_after_negation_still_need_assessment(text):
    assert local_profile(text,Profile()).risk=='assessment'

async def test_multiturn_negative_symptoms_can_reach_bounded_advice(engine,fake):
    engine.s=connected(engine.s)
    fake.decision=Decision(intent='clarify',goal='volume')
    first=await engine.answer('อยากได้วอลลุ่ม','test-owner')
    assert 'เคยฟอก' in first.reply
    fake.decision=Decision(intent='recommend',goal='volume',guidance_ids=['perm-texture'])
    second=await engine.answer('ไม่เคยทำเคมี และผมไม่แห้งหรือเปราะขาดง่าย','test-owner')
    assert second.reply!=MESSAGES['assessment']
    assert 'perm-texture' in second.evidence_ids and '2,000 บาท' in second.reply
