import re
import unicodedata

MESSAGES = {
    'unknown': 'ยังไม่มีข้อมูลที่ยืนยันสำหรับคำถามนี้ในฐานความรู้ของร้าน UNNAMED ครับ กรุณาสอบถามร้านเพิ่มเติม',
    'blocked': 'บอตให้ข้อมูลบริการร้าน UNNAMED เท่านั้น และไม่สามารถเปิดเผยคำสั่งภายในหรือข้อมูลส่วนตัวได้ครับ',
    'unavailable': 'ขณะนี้ระบบตรวจสอบข้อมูลยังไม่พร้อม กรุณาลองใหม่อีกครั้งครับ',
    'greeting': 'สวัสดีครับ ยินดีต้อนรับสู่ UNNAMED สอบถามบริการ ราคา และข้อมูลร้านได้ครับ ข้อความจะประมวลผลผ่านผู้ให้บริการ AI กรุณาไม่ส่งข้อมูลส่วนตัวหรือสลิปครับ',
    'rate': 'ส่งคำถามถี่เกินไป กรุณารอสักครู่แล้วลองใหม่ครับ',
    'media': 'ขณะนี้ยังไม่เปิดรับภาพหรือสื่อชนิดนี้ กรุณาพิมพ์คำถามเกี่ยวกับบริการร้านครับ',
    'length': 'กรุณาส่งคำถามครั้งละไม่เกิน 1,000 ตัวอักษรครับ',
    'assessment': 'จากข้อมูลนี้ยังยืนยันความเหมาะสมของบริการเคมีไม่ได้ครับ กรุณาให้ช่างประเมินสภาพผมและประวัติการทำเคมีก่อน',
    'reset': 'ล้างบริบทการสนทนาของคุณแล้วครับ',
    'image_unclear': 'ยังอ่านลักษณะทรงผมจากภาพนี้ไม่ได้ชัดเจนครับ กรุณาส่งภาพทรงผมที่ชัดขึ้น หรือบอกเป้าหมายเป็นข้อความ',
    'image_missing': 'ขณะนี้ไม่มีข้อมูลภาพที่ใช้ประกอบคำถามครับ กรุณาส่งภาพทรงผมที่ชัดเจนอีกครั้ง รอข้อความยืนยันรับภาพ แล้วพิมพ์คำถามต่อได้เลยครับ',
    'image_expired': 'บริบทภาพอ้างอิงก่อนหน้าหมดอายุแล้วครับ กรุณาส่งภาพอีกครั้ง รอข้อความรับภาพ แล้วพิมพ์คำถามเกี่ยวกับภาพต่อได้เลยครับ',
    'image_received': 'รับภาพทรงผมแล้วครับ พิมพ์คำถามหรือสิ่งที่ต้องการเกี่ยวกับภาพนี้ต่อได้เลย เช่น “อยากได้ทรงนี้ ต้องทำบริการอะไร” ผมจะใช้ผลวิเคราะห์ภาพล่าสุดประกอบคำถามชั่วคราวครับ',
}


def normalize(text):
    # NFKC decomposes Thai SARA AM; restore it so Thai policy patterns still match.
    value = unicodedata.normalize('NFKC', text).lower().replace('\u0e4d\u0e32', '\u0e33')
    return re.sub(r'\s+', ' ', ''.join(c for c in value if unicodedata.category(c) != 'Cf')).strip()


def filtered(text):
    q = normalize(text)
    if re.search(r'system\s*prompt|developer\s*(prompt|message)|ignore.{0,40}(rule|instruction)|jailbreak|api.?key|access.?token|channel.?secret|พรอม[พป]ต์|คำสั่ง(ระบบ|ภายใน)|ลืม.{0,20}คำสั่ง|ข้อมูลลูกค้า|รายชื่อลูกค้า|ประวัติ.{0,15}(ลูกค้า|คนอื่น)|เบอร์.{0,15}(ลูกค้า|คนอื่น)|customer.{0,20}(data|list)|https?://', q):
        return None
    if re.search(r'\d(?:[\s().-]*\d){8,}|[\w.+-]+@[\w.-]+\.[a-z]{2,}|sk-(?:or-)?[a-z0-9_-]+|\bu[a-f0-9]{32}\b|เลขบัญชี|บัตรประชาชน|บ้านเลขที่|พาสปอร์ต|passport|line\s*id|ไลน์ไอดี|นามสกุล|ที่อยู่(ของ)?(ผม|ฉัน)', q):
        return None
    q = re.sub(r'(?:ผม|ฉัน|ดิฉัน|หนู)?ชื่อ\s*[^\s,，。]+', '', q)
    return q.strip()


def stated_hair_length(q):
    # Coarse conversation labels, not thresholds for chemical suitability.
    # Accept measurements only as a direct length reply or explicit hair length,
    # never a price, treatment duration, hypothetical, or reference to others.
    ending = r'\s*(?:ครับ|ค่ะ|คับ)?[.!?]?'
    prefix = r'(?:ผม(?:ยาว)?|ความยาวผม|ผมด้านหน้า(?:ยาว)?)?\s*(?:ยาว)?\s*(?:ประมาณ|ราว|สัก|ถึง|ระดับ|ยาวถึง)?\s*'
    landmark = re.fullmatch(prefix + r'(จมูก|ปลายจมูก|คิ้ว|ตา|หู|คาง|บ่า|ไหล่)' + ending, q)
    if landmark:
        return 'medium'
    measurement = re.fullmatch(prefix + r'(\d{1,3}(?:\.\d+)?)\s*(?:เซนติเมตร|เซน|ซม\.?|cm)' + ending, q)
    if measurement:
        cm = float(measurement[1])
        if 0 < cm <= 100:
            return 'short' if cm <= 5 else 'medium' if cm <= 25 else 'long'
    return None


def local_profile(q, profile):
    p = profile.model_copy()
    # Remove only the negated desire clause, retaining any following positive
    # desire. A declined look is never authoritative evidence of a desired goal.
    positive_goals = re.sub(r'(?:ไม่อยาก|ไม่ต้องการ|ไม่เอา|ไม่ได้อยาก)(?:(?!อยาก|ต้องการ|แต่|และ|[,;]).)*', '', q)
    for goal, pattern in [('volume', r'วอลลุ่ม|วอลุ่ม|วอลลุม|volume'), ('curls', r'อยาก.{0,20}ลอน|อยาก.{0,20}หยิก'), ('smooth', r'ชี้ฟู|นุ่มลื่น'), ('straight', r'อยาก.{0,15}(ตรง|เรียบ)')]:
        if re.search(pattern, positive_goals):
            p.goal = goal
    declined = set(p.declined_services)
    for service, pattern in [('perm', r'ดัด|perm'), ('straightening', r'ยืด|straighten'), ('keratin', r'เคราติน|keratin'), ('coloring', r'ทำสี|ย้อม|colou?r'), ('bleach', r'ฟอก|bleach')]:
        rejection = r'(?:ไม่อยาก|ไม่ต้องการ|ไม่เอา|ไม่ทำ|do not want|don\x27t want)\s*(?:ทำ)?\s*(?:' + pattern + ')'
        if re.search(rejection, q):
            declined.add(service)
        elif re.search(r'เปลี่ยนใจ\s*(?:แล้ว)?\s*(?:อยาก|ยอม|ขอ)(?:ทำ)?\s*(?:' + pattern + ')', q):
            declined.discard(service)
    p.declined_services = sorted(declined)
    if re.search(r'(?:ไม่เคย|ไม่ได้)(?:ทำ)?เคมี', q):
        p.history = 'none'
    # Strip only the negated treatment, never swallow a later positive statement.
    risk_q = re.sub(r'(?:ไม่เคย|ไม่ได้)(?:ทำ)?(?:เคมี|ฟอก(?:ผม)?|ทำสี(?:ผม)?|ดัด(?:ผม)?|ยืด(?:ผม)?)', '', q)
    # A single negation can cover a short list (ไม่แห้งหรือเปราะขาดง่าย).
    # Stop at clause boundaries so a later positive symptom remains visible.
    symptom = r'(?:แห้ง|เปราะ(?:ขาดง่าย)?|ขาดง่าย|ผมเสีย(?!ใจ)|แพ้)'
    risk_q = re.sub(r'(?:ไม่|ไม่ได้)\s*' + symptom + r'(?:\s*(?:หรือ|และ)\s*' + symptom + r')*', '', risk_q)
    if re.search(r'ฟอก|เปราะ|ขาดง่าย|ผมเสีย(?!ใจ)|แพ้|bleach|damaged|breakage', risk_q):
        p.risk = 'assessment'
        p.history = 'chemical'
    elif re.search(r'เคย.{0,10}(ดัด|ยืด|ทำสี)|(?:ทำสี|ยืด(?:ผม)?|ดัด(?:ผม)?).{0,6}(มา|แล้ว|เมื่อ)', risk_q):
        p.history = 'chemical'
        p.risk = 'assessment'
    for length, pattern in [('short', 'ผมสั้น'), ('medium', 'ผมยาวปานกลาง|ผมประบ่า'), ('long', 'ผมยาวมาก|ผมยาวเลย')]:
        if re.search(pattern, q):
            p.hair_length = length
    for length, pattern in [('short', 'สั้น'), ('medium', 'ปานกลาง|ประบ่า'), ('long', 'ยาวมาก|ยาวเลยไหล่')]:
        if re.fullmatch(f'(?:{pattern})(?:ครับ|ค่ะ|คับ)?', q):
            p.hair_length = length
    stated = stated_hair_length(q)
    if stated:
        p.hair_length = stated
    return p


def consultation(q):
    return bool(re.search(r'แนะนำ|เหมาะ|ทำไง|ทำยังไง|ขี้เกียจ.{0,15}เซ็[ตท]|ไม่อยาก.{0,15}เซ็[ตท]|ผม(เสีย(?!ใจ)|แห้ง|ชี้ฟู|ตรงมาก|ลีบ)|อยาก.{0,20}(ลอน|วอล|ตรง|เรียบ)', q))


TOPICS = {
    'phone': ['เบอร์', 'โทร', 'ติดต่อ', 'phone'], 'location': ['พิกัด', 'แผนที่', 'ร้านอยู่', 'อยู่ไหน', 'ที่อยู่ร้าน', 'location'],
    'hours': ['เปิด', 'ปิด', 'วันหยุด', 'hours'], 'parking': ['ที่จอด', 'จอดรถ'],
    'booking': ['จอง', 'คิว', 'booking'], 'promotion': ['โปรโมชั่น', 'โปรโม', 'ส่วนลด', 'มีโปร', 'โปรอะไร', 'โปรทั้งหมด', 'promotion', 'deals'],
}


def lexical_ids(q, kb):
    docs = kb.documents()
    hits = []
    for s in kb.store['services']:
        for alias in s['aliases']:
            a = normalize(alias)
            for match in re.finditer(re.escape(a), q):
                start = match.start()
                hits.append((s['id'], start, start + len(a)))
    ids = {h[0] for h in hits if not any(o[0] != h[0] and o[1] <= h[1] and o[2] >= h[2] and o[2] - o[1] > h[2] - h[1] for o in hits)}
    topics = [t for t, aliases in TOPICS.items() if any(a in q for a in aliases)]
    if re.search(r'^โปร(?=ดัด|ยืด|เครา|ฟอก|ทำสี)', q) and 'promotion' not in topics:
        topics.append('promotion')
    # A named package wins over its contained regular-service aliases. Broad
    # service questions include related offers, while promotion lists stay scoped.
    named_promos = {s['id'] for s in kb.store['services'] if s.get('category') == 'promotion' and s['id'] in ids}
    related_promos = {s['id'] for s in kb.store['services'] if s.get('category') == 'promotion' and s.get('relatedServiceId') in ids}
    if 'promotion' in topics:
        topics.remove('promotion')
        ids = named_promos or related_promos or {d.id for d in docs.values() if d.topic == 'promotion'}
    else:
        ids.update(related_promos)
    if 'parking' in topics and 'location' in topics and not re.search(r'พิกัดร้าน|แผนที่ร้าน|ร้านอยู่|ที่อยู่ร้าน', q):
        topics.remove('location')
    ids.update(d.id for d in docs.values() if d.topic in topics)
    if not ids and not topics and re.search(r'บริการอะไร|บริการทั้งหมด|เมนู|^(ราคา|บริการ)(ครับ|ค่ะ)?$|ราคาทั้งหมด', q):
        ids.update(s['id'] for s in kb.store['services'])
    missing = [t for t in topics if not any(d.topic == t for d in docs.values())]
    if any(docs[i].topic == 'promotion' for i in ids) and re.search(r'เงื่อนไข|หมดเขต|หมดโปร|วันหมดอายุ|ใช้ถึง|มัดจำ|เพิ่มเงิน|conditions|expires|expiry', q):
        missing.append('promotion-conditions')
    return [i for i in docs if i in ids], missing
