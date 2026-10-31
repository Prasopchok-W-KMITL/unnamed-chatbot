"""Bounded, content-free request traces. Never pass user/provider text here."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from functools import wraps
import json
import logging
from logging.handlers import RotatingFileHandler
import time
import uuid

logger = logging.getLogger('unnamed.diagnostics')
current = ContextVar('diagnostic_trace', default=None)
STAGES = {'request', 'queue', 'local_policy', 'profile', 'guard_input', 'retrieval', 'planner',
          'render', 'guard_output', 'store_profile', 'image_decode', 'image_guard',
          'vision', 'image_context', 'image_download', 'line_loading', 'line_reply', 'worker'}
REASONS = {'none', 'local_filter', 'empty_input', 'length_limit', 'reset', 'greeting',
           'local_self_harm', 'local_distress', 'guard_self_harm', 'guard_unsafe',
           'promotion_s6_exception', 'no_verified_evidence', 'invalid_evidence',
           'incomplete_evidence', 'invalid_schema', 'invalid_json', 'invalid_response',
           'http_error', 'rate_limited', 'authentication', 'credit_limit',
           'provider_unavailable', 'timeout', 'network_error', 'response_too_large',
           'provider_error_envelope', 'configuration', 'budget_day', 'budget_minute',
           'budget_exhausted', 'invalid_image', 'image_unsupported', 'images_disabled',
           'delivery_uncertain', 'job_expired', 'internal_error', 'cancelled',
           'image_expired', 'loading_unavailable', 'retry_deadline', 'retry_wait_too_long',
           'invalid_retry_after', 'verified_public_contact_masked'}
OUTCOMES = {'ok', 'blocked', 'unavailable', 'unknown', 'clarify', 'recommend', 'facts',
            'support', 'reset', 'greeting', 'image_received', 'image_unclear', 'media',
            'length', 'rate', 'assessment', 'uncertain', 'expired', 'cancelled', 'image_expired'}
ROLES = {'guard', 'image_guard', 'retrieval', 'planner', 'vision'}
KINDS = {'text', 'image', 'api_text', 'line_text', 'line_image', 'line_other'}


@dataclass
class Trace:
    request_id: str
    kind: str
    outcome: str = 'ok'
    stage: str = 'request'
    failed_stage: str | None = None
    deadline: float | None = None


def request_clock():
    try:
        return asyncio.get_running_loop().time()
    except RuntimeError:
        return time.monotonic()


def record(event, *, stage=None, reason=None, outcome=None, duration_ms=None,
           status=None, count=None, role=None, categories=None, attempt=None, wait_ms=None):
    if event not in {'request_started', 'request_finished', 'stage_finished',
                     'decision', 'failure', 'provider_error', 'budget_exhausted',
                     'retry_scheduled', 'retry_attempt', 'retry_skipped'}:
        raise ValueError('Unknown diagnostic event')
    trace = current.get()
    if stage is None and trace: stage = trace.stage
    row = {'time': datetime.now(timezone(timedelta(hours=7))).isoformat(timespec='milliseconds'),
           'event': event, 'request_id': trace.request_id if trace else None}
    if trace: row['kind'] = trace.kind
    if stage in STAGES: row['stage'] = stage
    if reason in REASONS: row['reason'] = reason
    if outcome in OUTCOMES: row['outcome'] = outcome
    if role in ROLES: row['role'] = role
    for key, value in [('duration_ms', duration_ms), ('status', status), ('count', count),
                       ('attempt', attempt), ('wait_ms', wait_ms)]:
        if type(value) in {int, float} and value >= 0:
            row[key] = round(value, 2)
    # Only fixed Llama Guard category codes; never a provider's explanation.
    if categories is not None:
        row['categories'] = sorted(set(categories) & {f'S{i}' for i in range(1, 15)})
    logger.log(logging.WARNING if event in {'failure', 'provider_error', 'budget_exhausted'}
               else logging.INFO, json.dumps(row, ensure_ascii=True, separators=(',', ':')))


def reason_for(error):
    reason = getattr(error, 'reason', None)
    if reason in REASONS: return reason
    if isinstance(error, TimeoutError): return 'timeout'
    from pydantic import ValidationError
    if isinstance(error, ValidationError): return 'invalid_schema'
    if isinstance(error, ValueError): return 'invalid_evidence'
    return 'internal_error'


def decide(outcome, reason='none', stage='local_policy', categories=None):
    trace = current.get()
    if trace and outcome in OUTCOMES: trace.outcome = outcome
    record('decision', stage=stage, outcome=outcome, reason=reason, categories=categories)


@contextmanager
def request_trace(kind, *, budget_seconds=40):
    # Engine calls inside a worker/API request share that request's random ID.
    parent = current.get()
    if parent is not None:
        yield parent
        return
    trace = Trace(uuid.uuid4().hex, kind if kind in KINDS else 'line_other',
                  deadline=request_clock()+budget_seconds)
    token = current.set(trace)
    start = time.perf_counter()
    record('request_started', stage='request')
    try:
        yield trace
    except asyncio.CancelledError:
        trace.outcome = 'cancelled'
        record('failure', stage=trace.failed_stage or trace.stage, reason='cancelled')
        raise
    except Exception as error:
        trace.outcome = 'unavailable'
        record('failure', stage=trace.failed_stage or trace.stage, reason=reason_for(error))
        raise
    finally:
        record('request_finished', stage='request', outcome=trace.outcome,
               duration_ms=(time.perf_counter()-start)*1000)
        current.reset(token)


@contextmanager
def span(stage):
    trace = current.get()
    previous = trace.stage if trace else None
    if trace: trace.stage = stage
    start = time.perf_counter()
    reason = 'none'
    try:
        yield
    except asyncio.CancelledError:
        reason = 'cancelled'
        if trace and not trace.failed_stage: trace.failed_stage = stage
        raise
    except Exception as error:
        reason = reason_for(error)
        if trace and not trace.failed_stage: trace.failed_stage = stage
        raise
    finally:
        record('stage_finished', stage=stage, reason=reason,
               duration_ms=(time.perf_counter()-start)*1000)
        if trace: trace.stage = previous


def traced(kind):
    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            with request_trace(kind):
                return await fn(*args, **kwargs)
        return wrapped
    return decorate


@contextmanager
def diagnostic_file(settings):
    """One rotating handler per app lifespan, removed and closed on shutdown."""
    handler = None
    if settings.diagnostics_enabled:
        target = settings.data_dir / 'runtime' / 'diagnostics.jsonl'
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(target, maxBytes=1_000_000, backupCount=3,
                                      encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield
    finally:
        if handler:
            logger.removeHandler(handler)
            handler.close()
