"""Fixed safety replies; never use customer text to generate crisis advice."""
import re
from dataclasses import dataclass
from .policy import normalize


# Thai contact verified with the local-hotline connector on 2026-10-12.
# Plain text is intentional: LINE text messages do not render Markdown links.
SELF_HARM_REPLY = (
    'ขอบคุณที่บอกความรู้สึกนะครับ ฟังดูว่าตอนนี้คุณกำลังทุกข์ใจมาก '
    'ตอนนี้คุณอยู่ในที่ปลอดภัยไหมครับ?\n\n'
    'หากกลัวว่าจะทำร้ายตัวเองตอนนี้ ขอให้อยู่ห่างจากสิ่งที่อาจใช้ทำร้ายตัวเอง '
    'และติดต่อคนที่ไว้ใจให้มาอยู่ด้วยทันทีครับ '
    'ถ้าบาดเจ็บหรือไม่ปลอดภัย ให้ขอคนใกล้ตัวช่วยพาไปห้องฉุกเฉินทันที\n\n'
    'คุณติดต่อสายด่วนสุขภาพจิต 1323 ได้ตลอด 24 ชั่วโมง เพื่อคุยกับผู้ให้คำปรึกษาครับ'
)

DISTRESS_REPLY = (
    'ได้ยินว่าคุณกำลังเสียใจครับ ขอบคุณที่บอกความรู้สึก '
    'ถ้าสะดวก เล่าเพิ่มได้ไหมครับว่าตอนนี้เกิดอะไรขึ้น?'
)


@dataclass(frozen=True)
class GuardVerdict:
    safe: bool
    categories: frozenset[str] = frozenset()

    @classmethod
    def parse(cls, value):
        # Only an exact safe label permits further processing. Malformed output
        # still blocks, and cannot manufacture a safety-category route.
        if not isinstance(value, str):
            return cls(False)
        label = value.strip()
        if label == 'safe':
            return cls(True)
        if re.fullmatch(r'unsafe\s*\n\s*S(?:[1-9]|1[0-4])(?:\s*,\s*S(?:[1-9]|1[0-4]))*', label):
            return cls(False, frozenset(re.findall(r'S\d+', label)))
        return cls(False)


_THAI_INTENT = re.compile(
    r'(?:อยาก|คิด(?:ที่)?จะ|กำลังจะ|จะ)\s*'
    r'(?:จบชีวิต(?:ตัวเอง)?|ฆ่าตัวตาย|ทำร้ายตัวเอง|กรีด(?:แขน|ข้อมือ))'
    r'|(?:อยาก|คิด(?:ที่)?จะ)\s*ตาย'
    r'|(?:ขอ|บอก|ช่วยบอก)?\s*วิธี\s*(?:ฆ่าตัวตาย|จบชีวิต|ทำร้ายตัวเอง)'
    r'|ไม่อยาก\s*(?:มีชีวิต|อยู่ต่อ|อยู่แล้ว)'
    r'|อยาก\s*หายไปจากโลกนี้'
    r'|โลกนี้\s*ไม่มี(?:ผม|ฉัน|หนู|เรา)\s*(?:อีกต่อไป|แล้ว)'
)
_THAI_NEGATION = re.compile(r'(?:ไม่ได้|ไม่เคย|ไม่)\s*$')
_CONDITIONAL_WATER_THREAT = re.compile(
    r'^(?:ถ้า|หาก).{0,60}(?:ไม่ได้|ไม่ยอม|ไม่ให้|ไม่ตอบ|ไม่ช่วย).{0,25}'
    r'(?:ผม|ฉัน|หนู|เรา)?\s*จะ\s*(?:ไป)?\s*(?:กระโดด|โดด)(?:ลง)?(?:น้ำ|แม่น้ำ|คลอง|สะพาน)'
)
_WATER_RECREATION = re.compile(r'เล่น|ว่ายน้ำ|สระว่ายน้ำ|ฝึก|ออกกำลังกาย|ดำน้ำ|เที่ยว|กีฬา')
_ENGLISH_INTENT = re.compile(
    r"\b(?:i\s+(?:(?:want|plan|intend|am going)\s+to|will)|i['’]m going to)\s+"
    r'(?:kill myself|end my life|hurt myself|harm myself|die)\b'
    r"|\bi\s+(?:don['’]t|do not)\s+want\s+to\s+(?:live|be alive)\b"
    r'|\bhow\s+(?:can i|do i|to)\s+(?:kill myself|end my life|harm myself)\b'
    r'|\b(?:the )?world would be better without me\b'
)


def explicit_self_harm(text):
    """Narrow local fallback, not a diagnosis or complete risk classifier.

    Run before privacy filters so mixed sensitive/injected text gets a fixed
    supportive reply without exposing it to any model or retrieval service.
    """
    q = normalize(text)
    for match in _THAI_INTENT.finditer(q):
        if not _THAI_NEGATION.search(q[max(0, match.start() - 12):match.start()]):
            return True
    if _CONDITIONAL_WATER_THREAT.search(q) and not _WATER_RECREATION.search(q):
        return True
    return bool(_ENGLISH_INTENT.search(q))


def simple_distress(text):
    """A short emotional statement, not hair damage or a diagnosis."""
    return bool(re.fullmatch(
        r'(?:ผม|ฉัน|หนู|เรา)?\s*(?:รู้สึก)?\s*(?:เสียใจ|เศร้า|รู้สึกแย่)'
        r'\s*(?:มาก|มากๆ|มาก ๆ|จัง|เหลือเกิน)?\s*(?:เลย)?\s*(?:นะ|ครับ|ค่ะ|คับ|นะครับ|นะคะ)?[.!?]*',
        normalize(text)))
