import pytest
from app.policy import lexical_ids, MESSAGES
from app.schemas import Decision
from conftest import connected

PROMOS = {'promo-bleach-one-color', 'promo-bleach-two-color', 'promo-keratin-haircut', 'promo-perm-haircut', 'promo-straightening-haircut'}

@pytest.mark.parametrize('q,id,price,time', [
    ('ฟอกหนึ่งครั้ง', 'promo-bleach-one-color', '2,499', '90 นาทีขึ้นไป'),
    ('ฟอกหนึ่งครั้งพร้อมทำสีราคาเท่าไหร่', 'promo-bleach-one-color', '2,499', '90 นาทีขึ้นไป'),
    ('2 bleaches + color', 'promo-bleach-two-color', '2,999', '90 นาทีขึ้นไป'),
    ('Keratin free haircut', 'promo-keratin-haircut', '2,400', '180 นาทีขึ้นไป'),
    ('ดัดฟรีตัด', 'promo-perm-haircut', '2,000', '180 นาที'),
    ('ยืดฟรีตัด', 'promo-straightening-haircut', '2,400', '150 นาที'),
])
async def test_package_facts(engine, q, id, price, time):
    r = await engine.answer(q, 'one')
    assert r.evidence_ids == [id]
    assert price + ' บาท' in r.reply and time in r.reply
    assert 'โปรโมชั่น:' in r.reply
    assert 'ฟรีตัดผม' in r.reply if 'haircut' in id else 'ฟรีตัดผม' not in r.reply

@pytest.mark.parametrize('q', ['มีโปรโมชั่นไหม', 'โปรทั้งหมด', 'promotions', 'มีโปรอะไรบ้าง'])
async def test_promotion_list(engine, q):
    r = await engine.answer(q, 'one')
    assert set(r.evidence_ids) == PROMOS
    assert 'April' not in r.reply

async def test_all_services_and_regular_prices(engine, kb):
    r = await engine.answer('บริการทั้งหมด', 'one')
    assert len(r.evidence_ids) == 16 and PROMOS <= set(r.evidence_ids)
    assert kb.store['services'][8]['priceBaht'] == 1000
    assert 'april-perm' not in kb.documents()
    assert len(kb.documents()) == 23

@pytest.mark.parametrize('q,ids', [
    ('ทำสีราคาเท่าไหร่', {'coloring','promo-bleach-one-color','promo-bleach-two-color'}),
    ('เคราตินราคาเท่าไหร่', {'keratin','promo-keratin-haircut'}),
    ('โปรดัดผม', {'promo-perm-haircut'}),
])
async def test_general_service_and_scoped_promo(engine, q, ids):
    r = await engine.answer(q, 'one')
    assert set(r.evidence_ids) == ids

async def test_unknown_conditions_not_invented(engine):
    r = await engine.answer('โปรโมชั่นมีเงื่อนไขอะไร หมดเขตเมื่อไหร่', 'one')
    assert 'ยังไม่มีข้อมูลยืนยัน' in r.reply

async def test_connected_complete_verified_offer_list(engine, fake):
    engine.s = connected(engine.s)
    fake.decision = Decision(intent='facts', fact_ids=['promo-perm-haircut'])
    r = await engine.answer('มีโปรโมชั่นไหม', 'one')
    assert set(r.evidence_ids) == PROMOS and r.guard_checked
    fake.docs = [engine.kb.documents()['promo-perm-haircut']]
    r = await engine.answer('มีโปรโมชั่นไหม', 'one')
    assert r.reply == MESSAGES['unavailable'] and not r.evidence_ids
