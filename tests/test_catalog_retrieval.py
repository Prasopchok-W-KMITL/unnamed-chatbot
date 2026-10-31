import json
import httpx
import pytest
from app.providers import Providers
from app.policy import MESSAGES
from conftest import connected

@pytest.mark.parametrize('question,slots,tokens', [
 ('มีบริการอะไรบ้าง\nเป้าหมายที่ลูกค้าระบุ: ยังไม่ทราบ',40,12000),
 ('บริการทั้งหมด',40,12000),
 ('ตัดผมราคาเท่าไหร่',20,4000),
 ('มีโปรโมชั่นอะไรบ้าง',20,4000),
])
async def test_catalog_retrieval_budget_only_expands_for_full_list(settings,kb,store,question,slots,tokens):
 def respond(request):
  payload=json.loads(request.content)
  assert payload['top_k']==payload['chunk_top_k']==slots
  assert payload['max_total_tokens']==tokens
  return httpx.Response(200,json={'status':'success','data':{'chunks':[
   {'file_path':kb.file_source(d),'content':d.indexed_text} for d in kb.documents().values()]}})
 async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as c:
  docs=await Providers(connected(settings),kb,store,c).retrieve(question)
 assert len(docs)==23

async def test_connected_service_catalog_requires_all_sixteen_verified_services(engine,fake):
 engine.s=connected(engine.s)
 r=await engine.answer('มีบริการอะไรบ้าง','one')
 assert set(r.evidence_ids)=={s['id'] for s in engine.kb.store['services']}
 assert r.guard_checked and len(r.evidence_ids)==16
 fake.docs=[d for d in engine.kb.documents().values() if d.id!='kids-haircut']
 r=await engine.answer('มีบริการอะไรบ้าง','one')
 assert r.reply==MESSAGES['unavailable'] and not r.evidence_ids
