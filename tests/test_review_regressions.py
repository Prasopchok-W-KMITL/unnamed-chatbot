import pytest
from pydantic import ValidationError
from app.schemas import Profile,Decision,VisionSummary
from app.policy import MESSAGES,local_profile,normalize
from conftest import connected

async def image_reference(engine,fake):
 engine.s=connected(engine.s).model_copy(update={'enable_images':True})
 fake.summary=VisionSummary(kind='hairstyle',hair_length='short',texture='wavy',volume='high')
 await engine.image(b'synthetic','one')

async def test_declined_treatment_survives_history_followup_and_reset(engine,fake,store):
 await image_reference(engine,fake)
 fake.decision=Decision(intent='recommend',goal='curls',guidance_ids=['perm-texture'])
 first=await engine.answer('อยากได้ทรงนี้ แต่ไม่อยากดัด','one')
 assert 'เคยฟอก' in first.reply
 assert store.get_profile('one').declined_services==['perm']
 second=await engine.answer('ไม่เคยทำเคมี','one')
 assert second.reply==MESSAGES['unknown'] and not second.evidence_ids
 assert [c for c in fake.calls if c[0]=='decide'][-1][2]['declined_services']==['perm']
 assert store.get_profile('two').declined_services==[]
 await engine.answer('เริ่มใหม่','one')
 assert store.get_profile('one').declined_services==[]

async def test_declined_treatment_expires_with_profile(store):
 store.set_profile('one',Profile(declined_services=['perm']))
 now=store.clock();store.clock=lambda:now+1801
 assert store.get_profile('one').declined_services==[]

@pytest.mark.parametrize('question', ['ทรงนี้ถ้าดัดผมราคาเท่าไหร่','อยากได้ลอนทรงนี้ ดัดผมราคาเท่าไหร่'])
async def test_price_from_image_stays_factual_without_chemical_history(engine,fake,question):
 await image_reference(engine,fake)
 fake.decision=Decision(intent='facts',fact_ids=['perm','promo-perm-haircut'])
 result=await engine.answer(question,'one')
 assert '2,000 บาท' in result.reply and 'เคยฟอก' not in result.reply
 assert set(result.evidence_ids)=={'perm','promo-perm-haircut'}

async def test_image_service_recommendation_not_confused_with_catalog(engine,fake):
 await image_reference(engine,fake)
 fake.decision=Decision(intent='facts',fact_ids=['perm'])
 result=await engine.answer('อยากได้ทรงนี้ ต้องทำบริการอะไร','one')
 assert 'เคยฟอก' in result.reply and not result.evidence_ids

@pytest.mark.parametrize('question,goal',[
 ('อยากเพิ่มวอลลุ่มแต่ไม่อยากได้ผมตรง','volume'),
 ('ไม่อยากได้ผมตรง แต่อยากได้ลอน','curls'),
 ('ไม่อยากมีวอลลุ่มแต่อยากได้ผมตรง','straight'),
 ('ไม่อยากได้ผมตรง','unknown'),
])
def test_negated_desires_are_not_explicit_goals(question,goal):
 assert local_profile(normalize(question),Profile()).goal==goal

async def test_negative_goal_no_longer_overrides_valid_recommendation(engine,fake,store):
 engine.s=connected(engine.s)
 store.set_profile('one',Profile(history='none'))
 fake.decision=Decision(intent='recommend',goal='volume',guidance_ids=['perm-texture'])
 result=await engine.answer('อยากเพิ่มวอลลุ่มแต่ไม่อยากได้ผมตรง','one')
 assert result.evidence_ids==['perm-texture','perm']
 assert store.get_profile('one').goal=='volume'

@pytest.mark.parametrize('question,ids',[
 ('เคราตินธรรมดากับเคราตินฟรีตัดต่างกันยังไง',{'keratin','promo-keratin-haircut'}),
 ('เคราตินฟรีตัดกับเคราตินธรรมดาต่างกันยังไง',{'keratin','promo-keratin-haircut'}),
 ('เคราตินฟรีตัดกับดัดผมราคาเท่าไหร่',{'promo-keratin-haircut','perm','promo-perm-haircut'}),
])
async def test_comparison_keeps_independent_regular_service_occurrences(engine,fake,question,ids):
 engine.s=connected(engine.s)
 fake.decision=Decision(intent='facts',fact_ids=['promo-keratin-haircut'])
 result=await engine.answer(question,'one')
 assert set(result.evidence_ids)==ids


def test_old_profile_compatible_and_planner_cannot_supply_declined_services():
 old={'goal':'volume','history':'none','hair_length':'unknown','risk':'unknown'}
 assert Profile.model_validate(old).declined_services==[]
 assert 'declined_services' not in Decision.model_json_schema()['properties']
 with pytest.raises(ValidationError):Decision.model_validate({'intent':'unknown','declined_services':['perm']})
 p=local_profile('ไม่อยากดัด',Profile())
 assert p.declined_services==['perm']
 assert local_profile('ไม่เคยทำเคมี',p).declined_services==['perm']
 assert local_profile('เปลี่ยนใจอยากดัด',p).declined_services==[]
