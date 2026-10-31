from app.schemas import Profile
import pytest


def test_queue_encrypts_deduplicates_and_deletes_payload(store):
    payload = {'kind': 'text', 'text': 'private customer message', 'reply_token': 'private-reply'}
    assert store.enqueue('event-1', 'private-user', payload) == 'queued'
    assert store.enqueue('event-1', 'private-user', payload) == 'duplicate'
    for path in store.path.parent.glob('bot.sqlite3*'):
        data = path.read_bytes()
        assert b'private' not in data and b'event-1' not in data
    job = store.claim()
    assert job['payload'] == payload
    assert store.delivering(job)
    store.finish(job, 'done')
    with store.db() as db:
        row = db.execute('SELECT * FROM jobs').fetchone()
        assert row['payload'] is None and row['status'] == 'done'


def test_rate_budget_and_same_user_serialization(store):
    for i in range(12): assert store.enqueue(str(i), 'one', {'kind': 'text', 'text': 'x', 'reply_token': 'r'}) == 'queued'
    assert store.enqueue('thirteen', 'one', {'kind': 'text', 'text': 'secret', 'reply_token': 'r'}) == 'rate'
    first = store.claim()
    assert store.claim() is None  # same user is still processing
    assert store.enqueue('other', 'two', {'kind': 'media', 'reply_token': 'r'}) == 'queued'
    assert store.claim()['owner'] != first['owner']
    store.settings.model_calls_per_day = 2
    assert store.reserve_budget('planner')
    assert store.reserve_budget('planner')
    assert not store.reserve_budget('planner')
    assert store.reserve_budget('guard')


def test_expired_jobs_and_ambiguous_delivery_never_retried(store):
    start = store.clock()
    store.enqueue('one', 'one', {'kind': 'media', 'reply_token': 'r'})
    job = store.claim()
    assert store.delivering(job)
    store.clock = lambda: start + 46
    assert store.claim() is None
    with store.db() as db: assert db.execute('SELECT status FROM jobs').fetchone()[0] == 'uncertain'
    store.enqueue('two', 'two', {'kind': 'media', 'reply_token': 'r'})
    store.clock = lambda: start + 102
    assert store.claim() is None


def test_processing_lease_recovery_and_restart(store, settings):
    from app.storage import Store
    start = store.clock()
    store.enqueue('one', 'one', {'kind': 'media', 'reply_token': 'r'})
    first = store.claim()
    restarted = Store(settings, clock=lambda: start + 46)
    recovered = restarted.claim()
    assert recovered['id'] == first['id'] and recovered['claim'] != first['claim']
    assert not store.delivering(first)


@pytest.mark.parametrize('role', ['guard', 'image_guard'])
def test_guard_daily_budget_is_independent_from_cloud_and_preserves_counts(store, role):
    now = store.clock()
    store.clock = lambda: now
    store.settings.model_calls_per_day = 2
    store.settings.guard_calls_per_day = 3
    for _ in range(2): assert store.reserve_budget('planner')
    assert not store.reserve_budget('planner')
    for _ in range(3): assert store.reserve_budget(role)
    assert not store.reserve_budget(role)
    # Increasing the cap makes room without erasing already-used attempts.
    store.settings.guard_calls_per_day = 4
    assert store.reserve_budget(role)
    assert not store.reserve_budget(role)
    assert not store.reserve_budget('planner')
    assert store.reserve_budget('vision')
    store.clock = lambda: now + 86400
    assert store.reserve_budget(role)
    assert store.reserve_budget('planner')


@pytest.mark.parametrize('role', ['guard', 'image_guard'])
def test_guard_minute_cap_is_separate_and_failed_reservation_is_atomic(store, role):
    now = int(store.clock() // 60) * 60
    store.clock = lambda: now
    store.settings.guard_calls_per_minute = 2
    for _ in range(2): assert store.reserve_budget(role)
    assert not store.reserve_budget(role)
    assert store.reserve_budget('planner')
    with store.db() as db:
        assert db.execute('SELECT count FROM counters WHERE id LIKE ?',
                          (f'budget:{role}:day:%',)).fetchone()[0] == 2
    store.clock = lambda: now + 60
    assert store.reserve_budget(role)
def test_retrieval_budget_independent_and_increase_preserves_counts(store, caplog):
    store.settings = store.settings.model_copy(update={'model_calls_per_day': 1, 'retrieval_calls_per_day': 2})
    assert store.reserve_budget('planner')
    assert not store.reserve_budget('planner')
    assert store.reserve_budget('retrieval')
    assert store.reserve_budget('retrieval')
    assert not store.reserve_budget('retrieval')
    assert 'budget_exhausted role=retrieval window=day limit=2' in caplog.text
    store.settings = store.settings.model_copy(update={'retrieval_calls_per_day': 3})
    assert store.reserve_budget('retrieval')
    assert not store.reserve_budget('retrieval')
    assert not store.reserve_budget('planner')
    assert store.reserve_budget('guard')


def test_retrieval_minute_cap_keeps_daily_counter_atomic(store, caplog):
    now = store.clock()
    store.clock = lambda: now
    store.settings = store.settings.model_copy(update={'retrieval_calls_per_minute': 1})
    assert store.reserve_budget('retrieval')
    assert not store.reserve_budget('retrieval')
    assert 'budget_exhausted role=retrieval window=minute limit=1' in caplog.text
    with store.db() as db:
        assert db.execute("SELECT count FROM counters WHERE id LIKE 'budget:retrieval:day:%'").fetchone()[0] == 1
    store.clock = lambda: now + 61
    assert store.reserve_budget('retrieval')

