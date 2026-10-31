"""Single-host SQLite outbox. Payloads/profiles encrypted; no chat transcript."""
import hashlib
import hmac
import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from cryptography.fernet import Fernet
from .knowledge import THAI
from .schemas import Profile, VisionSummary
from .diagnostics import record

log = logging.getLogger(__name__)

# Keep only a short content-free indication that a reference really expired.
# This is not image memory: it contains no image, summary or conversation text.
IMAGE_EXPIRY_NOTICE_SECONDS = 1800
MAX_IMAGE_EXPIRY_NOTICES = 1000


class Store:
    def __init__(self, settings, clock=time.time):
        if not settings.data_key or not settings.identity_hash_key:
            raise ValueError('Run python -m app.cli init first')
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = settings.data_dir / 'bot.sqlite3'
        self.cipher = Fernet(settings.data_key.encode())
        self.hash_key = settings.identity_hash_key.encode()
        self.clock, self.settings = clock, settings
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, payload BLOB,
                    status TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                    lease REAL DEFAULT 0, claim TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS profiles (owner TEXT PRIMARY KEY, payload BLOB NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS image_contexts (owner TEXT PRIMARY KEY, payload BLOB NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS image_expiry_notices (owner TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS image_expiry_notices_expiry ON image_expiry_notices(expires);
                CREATE TABLE IF NOT EXISTS counters (id TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
            ''')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=3)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def key(self, namespace, value):
        return hmac.new(self.hash_key, f'{namespace}:{value}'.encode(), hashlib.sha256).hexdigest()

    def pack(self, value):
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode())

    def unpack(self, value):
        return json.loads(self.cipher.decrypt(value))

    def cleanup(self, db):
        now = self.clock()
        db.execute('DELETE FROM profiles WHERE expires <= ?', (now,))
        db.execute('DELETE FROM image_expiry_notices WHERE expires <= ?', (now,))
        # Preserve only the expiry fact before any caller removes expired image
        # summaries (including get_profile/reserve_budget and the worker loop).
        db.execute('''INSERT OR REPLACE INTO image_expiry_notices (owner,expires)
                      SELECT owner,expires+? FROM image_contexts
                      WHERE expires<=? AND expires+?>?''',
                   (IMAGE_EXPIRY_NOTICE_SECONDS, now, IMAGE_EXPIRY_NOTICE_SECONDS, now))
        db.execute('DELETE FROM image_contexts WHERE expires <= ?', (now,))
        db.execute('''DELETE FROM image_expiry_notices WHERE owner IN
                      (SELECT owner FROM image_expiry_notices ORDER BY expires DESC,owner
                       LIMIT -1 OFFSET ?)''', (MAX_IMAGE_EXPIRY_NOTICES,))
        db.execute('DELETE FROM counters WHERE expires <= ?', (now,))
        db.execute("UPDATE jobs SET payload=NULL,status='expired',claim='' WHERE expires<=? AND payload IS NOT NULL", (now,))
        db.execute('DELETE FROM jobs WHERE created < ?', (now - 86400,))

    def get_profile(self, owner):
        with self.db() as db:
            self.cleanup(db)
            row = db.execute('SELECT payload FROM profiles WHERE owner=?', (owner,)).fetchone()
        return Profile.model_validate(self.unpack(row['payload'])) if row else Profile()

    def set_profile(self, owner, profile):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO profiles VALUES (?,?,?)',
                       (owner, self.pack(profile.model_dump()), self.clock() + self.settings.session_ttl_seconds))

    def reset(self, owner):
        with self.db() as db:
            db.execute('DELETE FROM profiles WHERE owner=?', (owner,))
            db.execute('DELETE FROM image_contexts WHERE owner=?', (owner,))
            db.execute('DELETE FROM image_expiry_notices WHERE owner=?', (owner,))

    def clear_image_context(self, owner):
        with self.db() as db:
            db.execute('DELETE FROM image_contexts WHERE owner=?', (owner,))
            db.execute('DELETE FROM image_expiry_notices WHERE owner=?', (owner,))

    def set_image_context(self, owner, summary):
        summary = VisionSummary.model_validate(summary.model_dump())
        if summary.kind != 'hairstyle':
            raise ValueError('Only hairstyle context is allowed')
        with self.db() as db:
            db.execute('DELETE FROM image_expiry_notices WHERE owner=?', (owner,))
            db.execute('INSERT OR REPLACE INTO image_contexts VALUES (?,?,?)',
                       (owner, self.pack(summary.model_dump()), self.clock() + self.settings.image_context_ttl_seconds))

    def get_image_context(self, owner):
        return self.get_image_context_state(owner)[0]

    def get_image_context_state(self, owner):
        """Return (active summary, genuinely expired); never revive old enums."""
        with self.db() as db:
            self.cleanup(db)
            row = db.execute('SELECT payload FROM image_contexts WHERE owner=?', (owner,)).fetchone()
            expired = db.execute('SELECT 1 FROM image_expiry_notices WHERE owner=?', (owner,)).fetchone() is not None
        return (VisionSummary.model_validate(self.unpack(row['payload'])) if row else None, expired)

    def reserve_budget(self, role):
        now = self.clock()
        day = datetime.fromtimestamp(now, THAI).date().isoformat()
        is_guard = role in {'guard', 'image_guard'}
        daily_limit = self.settings.guard_calls_per_day if is_guard else self.settings.model_calls_per_day
        minute_limit = self.settings.guard_calls_per_minute if is_guard else 15
        if role == 'retrieval':
            daily_limit = self.settings.retrieval_calls_per_day
            minute_limit = self.settings.retrieval_calls_per_minute
        keys = [(f'budget:{role}:day:{day}', daily_limit, now + 172800),
                (f'budget:{role}:minute:{int(now // 60)}', minute_limit, now + 120)]
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.cleanup(db)
            for key, maximum, _ in keys:
                row = db.execute('SELECT count FROM counters WHERE id=?', (key,)).fetchone()
                if row and row['count'] >= maximum:
                    log.warning('budget_exhausted role=%s window=%s limit=%s', role, 'day' if ':day:' in key else 'minute', maximum)
                    record('budget_exhausted', role=role, reason='budget_day' if ':day:' in key else 'budget_minute')
                    return False
            for key, _, expires in keys:
                db.execute('INSERT INTO counters VALUES (?,1,?) ON CONFLICT(id) DO UPDATE SET count=count+1', (key, expires))
        return True

    def enqueue(self, event_id, user_id, payload):
        now = self.clock()
        event_key, owner = self.key('event', event_id), self.key('user', user_id)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.cleanup(db)
            if db.execute('SELECT 1 FROM jobs WHERE id=?', (event_key,)).fetchone():
                return 'duplicate'
            pending = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','processing','delivering')").fetchone()[0]
            if pending >= 100:
                return 'busy'
            counter = f'user:{owner}:{int(now // 60)}'
            row = db.execute('SELECT count FROM counters WHERE id=?', (counter,)).fetchone()
            rate = bool(row and row['count'] >= 12)
            db.execute('INSERT INTO counters VALUES (?,1,?) ON CONFLICT(id) DO UPDATE SET count=count+1', (counter, now + 120))
            if rate:
                payload = {'kind': 'rate', 'reply_token': payload['reply_token']}
            db.execute('INSERT INTO jobs (id,owner,payload,status,created,expires) VALUES (?,?,?,?,?,?)',
                       (event_key, owner, self.pack(payload), 'queued', now, now + 55))
        return 'rate' if rate else 'queued'

    def claim(self):
        now = self.clock()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            self.cleanup(db)
            # Never retry a job whose reply may already have reached LINE.
            db.execute("UPDATE jobs SET status='uncertain',payload=NULL WHERE status='delivering' AND lease<=?", (now,))
            db.execute("UPDATE jobs SET status='queued',claim='' WHERE status='processing' AND lease<=?", (now,))
            row = db.execute("""SELECT j.* FROM jobs j WHERE j.status='queued' AND NOT EXISTS
                (SELECT 1 FROM jobs other WHERE other.owner=j.owner AND other.status IN ('processing','delivering'))
                ORDER BY j.created,j.rowid LIMIT 1""").fetchone()
            if not row:
                return None
            claim = uuid.uuid4().hex
            db.execute("UPDATE jobs SET status='processing',claim=?,lease=? WHERE id=?", (claim, now + 45, row['id']))
            return {**dict(row), 'claim': claim, 'payload': self.unpack(row['payload'])}

    def delivering(self, job):
        with self.db() as db:
            result = db.execute("UPDATE jobs SET status='delivering' WHERE id=? AND claim=? AND status='processing' AND expires>?",
                                (job['id'], job['claim'], self.clock()))
            return result.rowcount == 1

    def finish(self, job, status):
        if status not in {'done', 'failed', 'uncertain'}:
            raise ValueError('Invalid terminal state')
        with self.db() as db:
            db.execute('UPDATE jobs SET status=?,payload=NULL WHERE id=? AND claim=?', (status, job['id'], job['claim']))
