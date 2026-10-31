import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

THAI = timezone(timedelta(hours=7))


@dataclass(frozen=True)
class Document:
    id: str
    topic: str
    text: str
    source: str
    goals: tuple[str, ...] = ()
    service_id: str = ''

    @property
    def marker(self):
        return f'UNNAMED_EVIDENCE:{self.id}:{hashlib.sha256(self.text.encode()).hexdigest()}'

    @property
    def indexed_text(self):
        return f'{self.marker}\n{self.text}'


class Knowledge:
    def __init__(self, directory: Path):
        self.store = json.loads((directory / 'store.json').read_text(encoding='utf-8'))
        self.advice = json.loads((directory / 'advice.json').read_text(encoding='utf-8'))
        if self.store.get('storeName') != 'UNNAMED' or self.store.get('schemaVersion') != 1:
            raise ValueError('Invalid public knowledge')
        ids = set()
        for item in [*self.store['services'], *self.store['facts'], *self.store['promotions'], *self.advice['guidance']]:
            if not re.fullmatch(r'[a-z][a-z0-9-]{1,63}', item['id']) or item['id'] in ids:
                raise ValueError('Duplicate/invalid evidence ID')
            ids.add(item['id'])
        for s in self.store['services']:
            if s.get('category', 'regular') not in {'regular', 'promotion'}:
                raise ValueError('Invalid service category')
            if s.get('relatedServiceId') and s['relatedServiceId'] not in {item['id'] for item in self.store['services'] if item.get('category', 'regular') == 'regular'}:
                raise ValueError('Invalid related service')
            if not s['name'] or not s['aliases'] or type(s['durationMinutes']) is not int or not 0 < s['durationMinutes'] <= 1440 or type(s['minimumDuration']) is not bool:
                raise ValueError('Invalid service')
            if s['priceBaht'] is not None and (type(s['priceBaht']) is not int or s['priceBaht'] < 0):
                raise ValueError('Invalid price')
        for f in [*self.store['facts'], *self.store['promotions']]:
            if type(f['confirmed']) is not bool or (f['confirmed'] and not f['text'].strip()):
                raise ValueError('Invalid fact')
            if f in self.store['promotions'] and f['confirmed']:
                if date.fromisoformat(f['validFrom']) > date.fromisoformat(f['validUntil']):
                    raise ValueError('Invalid promotion dates')
        service_ids = {s['id'] for s in self.store['services']}
        for g in self.advice['guidance']:
            from urllib.parse import urlparse
            url = urlparse(g['sourceUrl'])
            if g['serviceId'] not in service_ids or url.scheme != 'https' or url.hostname not in {'www.wella.com', 'www.kerasilk.com'} or not set(g['goals']) <= {'volume', 'curls', 'smooth', 'straight'}:
                raise ValueError('Invalid guidance source')
        self.questions = {q['id']: q['text'] for q in self.advice['questions']}
        if not {'desired-look', 'hair-history', 'styling-time'} <= self.questions.keys():
            raise ValueError('Missing clarification')

    def documents(self, now=None):
        today = (now or datetime.now(THAI)).astimezone(THAI).date().isoformat()
        docs = []
        for s in self.store['services']:
            price = f"ราคา {s['priceBaht']:,} บาท" if s['priceBaht'] is not None else 'ยังไม่มีราคาที่ยืนยัน กรุณาสอบถามร้านครับ'
            duration = f"{s['durationMinutes']} นาทีขึ้นไป" if s['minimumDuration'] else f"โดยประมาณ {s['durationMinutes']} นาที"
            promotional = s.get('category', 'regular') == 'promotion'
            label = 'โปรโมชั่น: ' if promotional else ''
            docs.append(Document(s['id'], 'promotion' if promotional else 'service', f"{label}{s['name']}\nระยะเวลาบริการ{duration}\n{price}", s['source']))
        for f in self.store['facts']:
            if f['confirmed']:
                docs.append(Document(f['id'], f['topic'], f['text'], f['source']))
        for f in self.store['promotions']:
            if f['confirmed'] and f['validFrom'] <= today <= f['validUntil']:
                docs.append(Document(f['id'], f['topic'], f['text'], f['source']))
        for g in self.advice['guidance']:
            docs.append(Document(g['id'], 'guidance', f"{g['text']}\nแหล่งข้อมูลทั่วไป: {g['sourceTitle']}\n{g['sourceUrl']}", g['sourceUrl'], tuple(g['goals']), g['serviceId']))
        return {d.id: d for d in docs}

    def revision(self):
        content = '\n'.join(d.indexed_text for d in self.documents().values())
        return hashlib.sha256(content.encode()).hexdigest()[:24]

    def file_source(self, doc):
        # Per-document version keeps unchanged documents usable after a price edit.
        # LightRAG deduplicates identical text and may keep its original source path.
        digest = hashlib.sha256(doc.indexed_text.encode()).hexdigest()[:24]
        return f'unnamed/{digest}/{doc.id}.txt'
