"""Read-only readiness checks. No completion, embedding, query or LINE send calls."""
import argparse
import asyncio
from contextlib import closing
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit

from dotenv import dotenv_values
import httpx

from .config import ROOT, Settings
from .knowledge import Knowledge, THAI


class CheckFailure(Exception):
    def __init__(self, reason):
        self.reason = reason


async def metadata(client, url, headers=None):
    """Bound all reads; do not return error bodies, URLs or exception messages."""
    try:
        async with client.stream('GET', url, headers=headers or {}, timeout=4,
                                 follow_redirects=False) as response:
            if response.status_code != 200:
                raise CheckFailure({401: 'authentication_failed', 403: 'authentication_failed',
                                    429: 'rate_limited'}.get(response.status_code, 'http_error'))
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 250_000: raise CheckFailure('invalid_response')
        value = json.loads(body)
        if not isinstance(value, dict): raise CheckFailure('invalid_response')
        return value
    except CheckFailure:
        raise
    except httpx.TimeoutException:
        raise CheckFailure('timeout') from None
    except httpx.RequestError:
        raise CheckFailure('connection_failed') from None
    except Exception:
        raise CheckFailure('invalid_response') from None


def budget_snapshot(settings, now=None):
    """No Store construction, cleanup, reservation or writes to the live database."""
    now = time.time() if now is None else now
    path = settings.data_dir / 'bot.sqlite3'
    roles = ['guard', 'retrieval', 'planner']
    if settings.enable_images: roles += ['image_guard', 'vision']
    day = datetime.fromtimestamp(now, THAI).date().isoformat()
    counts = {}
    if path.exists():
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                for role in roles:
                    for window, key in [('day', day), ('minute', int(now//60))]:
                        row = db.execute('SELECT count FROM counters WHERE id=?',
                                         (f'budget:{role}:{window}:{key}',)).fetchone()
                        if row and (type(row[0]) is not int or row[0] < 0):
                            raise CheckFailure('database_unavailable')
                        counts[role, window] = row[0] if row else 0
        except CheckFailure: raise
        except sqlite3.Error:
            raise CheckFailure('database_unavailable') from None
    result = {}
    for role in roles:
        guard = role in {'guard', 'image_guard'}
        day_limit = settings.guard_calls_per_day if guard else settings.model_calls_per_day
        minute_limit = settings.guard_calls_per_minute if guard else 15
        if role == 'retrieval':
            day_limit, minute_limit = settings.retrieval_calls_per_day, settings.retrieval_calls_per_minute
        result[role] = {
            'day_used': counts.get((role, 'day'), 0), 'day_limit': day_limit,
            'minute_used': counts.get((role, 'minute'), 0), 'minute_limit': minute_limit,
        }
    return {'database_exists': path.exists(), 'roles': result}


async def check_readiness(settings, client, *, local_only=False, rag_config=None):
    checks = []
    def add(name, status, reason='ok', **details):
        checks.append({'check': name, 'status': status, 'reason': reason, **details})

    kb = Knowledge(settings.knowledge_dir)
    expected = len(kb.documents())
    add('knowledge', 'pass', services=len(kb.store['services']), documents=expected)
    try:
        snapshot = budget_snapshot(settings)
        exhausted = any(r['day_used'] >= r['day_limit'] or r['minute_used'] >= r['minute_limit']
                        for r in snapshot['roles'].values())
        add('local_budgets', 'fail' if exhausted else 'pass',
            'budget_exhausted' if exhausted else 'ok', **snapshot)
    except CheckFailure as error: add('local_budgets', 'fail', error.reason)

    try:
        health = await metadata(client, 'http://127.0.0.1:8000/healthz')
        if health.get('status') != 'ok' or health.get('mode') != settings.bot_mode:
            raise CheckFailure('backend_configuration_mismatch')
        status = await metadata(client, 'http://127.0.0.1:8000/api/status',
                                {'X-Admin-Key': settings.bot_admin_key})
        if (status.get('mode') != settings.bot_mode or status.get('documents') != expected
                or status.get('services') != len(kb.store['services'])
                or status.get('images_enabled') != settings.enable_images
                or status.get('knowledge_revision') != kb.revision()):
            raise CheckFailure('backend_restart_required')
        add('backend', 'pass')
        enabled = status.get('diagnostics_enabled') is True and status.get('diagnostics_version') == 1
        add('diagnostics', 'pass' if enabled else 'warn', 'ok' if enabled else 'diagnostics_not_enabled')
    except CheckFailure as error: add('backend', 'fail', error.reason)

    if settings.bot_mode != 'connected':
        add('connected_mode', 'warn', 'demo_mode')
        return finish(checks, True)

    rag = rag_config if rag_config is not None else dotenv_values(ROOT / '.env.lightrag')
    required = {settings.guard_model}
    if rag.get('LLM_BINDING') == 'ollama' and rag.get('EMBEDDING_BINDING') == 'ollama':
        required |= {rag.get('LLM_MODEL'), rag.get('EMBEDDING_MODEL')}
        if None in required or '' in required:
            add('ollama_configuration', 'fail', 'configuration_incomplete')
            required.discard(None); required.discard('')
        try:
            # This host-side checker targets the documented local Ollama setup.
            root = 'http://127.0.0.1:11434'
            tags = await metadata(client, root + '/api/tags')
            installed = model_names(tags)
            missing = required - installed
            add('ollama_models', 'fail' if missing else 'pass',
                'models_missing' if missing else 'ok', required_count=len(required), missing_count=len(missing))
            loaded = model_names(await metadata(client, root + '/api/ps'))
            add('ollama_loaded', 'pass' if required <= loaded else 'warn',
                'ok' if required <= loaded else 'warmup_recommended', loaded_required_count=len(required & loaded))
        except CheckFailure as error: add('ollama_models', 'fail', error.reason)
    else:
        add('ollama_models', 'warn', 'local_ollama_configuration_not_detected')

    # A different Guard URL must not be silently reported as the local one.
    if settings.guard_base_url.rstrip('/') != 'http://127.0.0.1:11434/v1':
        try:
            headers = {'Authorization': 'Bearer '+settings.guard_api_key} if settings.guard_api_key else {}
            value = await metadata(client, settings.guard_base_url.rstrip('/')+'/models', headers)
            rows = value.get('data')
            if not isinstance(rows, list) or not any(isinstance(row, dict) and row.get('id') == settings.guard_model for row in rows):
                raise CheckFailure('models_missing')
            add('guard_model', 'pass', 'inference_not_verified')
        except CheckFailure as error: add('guard_model', 'fail', error.reason)

    try:
        if not settings.lightrag_api_key: raise CheckFailure('configuration_incomplete')
        counts = (await metadata(client, settings.lightrag_url.rstrip('/')+'/documents/status_counts',
                                {'X-API-Key': settings.lightrag_api_key})).get('status_counts')
        if not isinstance(counts, dict) or any(type(v) is not int or v < 0 for v in counts.values()):
            raise CheckFailure('invalid_response')
        # LightRAG versions serialize DocStatus values with different case.
        normalized = {}
        for key, value in counts.items():
            if not isinstance(key, str): raise CheckFailure('invalid_response')
            normalized[key.upper()] = normalized.get(key.upper(), 0) + value
        processed = normalized.get('PROCESSED', 0)
        # ALL is LightRAG's aggregate count, not a document status.
        unfinished = sum(v for key,v in normalized.items() if key not in {'PROCESSED', 'ALL'})
        ok = processed >= expected and unfinished == 0
        add('lightrag_documents', 'pass' if ok else 'fail', 'count_only' if ok else 'documents_not_ready',
            expected_count=expected, processed=processed, unfinished=unfinished)
    except CheckFailure as error: add('lightrag_documents', 'fail', error.reason)

    if settings.enable_images:
        configured = settings.image_guard_provider == 'openai' and bool(settings.openai_moderation_api_key)
        add('image_moderation', 'warn' if configured else 'fail',
            'authentication_not_verified' if configured else 'configuration_incomplete')

    if local_only:
        add('cloud_metadata', 'skipped', 'local_only')
    else:
        try:
            if not settings.openrouter_api_key: raise CheckFailure('configuration_incomplete')
            data = (await metadata(client, 'https://openrouter.ai/api/v1/key',
                                   {'Authorization': 'Bearer '+settings.openrouter_api_key})).get('data')
            if not isinstance(data, dict): raise CheckFailure('invalid_response')
            limit, remaining = data.get('limit'), data.get('limit_remaining')
            if any(v is not None and (type(v) not in {int,float} or not math.isfinite(v) or v < 0)
                   for v in (limit,remaining)):
                raise CheckFailure('invalid_response')
            if remaining is not None and remaining <= 0:
                add('openrouter_key', 'fail', 'credit_limit_exhausted', limit_usd=limit, remaining_usd=remaining)
            elif limit is None or limit > 1:
                add('openrouter_key', 'warn', 'key_limit_above_project_budget', limit_usd=limit, remaining_usd=remaining)
            elif remaining is None:
                add('openrouter_key', 'warn', 'credit_remaining_unknown', limit_usd=limit)
            else:
                add('openrouter_key', 'pass', 'inference_not_verified', limit_usd=limit, remaining_usd=remaining)
        except CheckFailure as error: add('openrouter_key', 'fail', error.reason)
        try:
            headers = {'Authorization': 'Bearer '+settings.line_new_channel_access_token}
            bot = await metadata(client, 'https://api.line.me/v2/bot/info', headers)
            if bot.get('userId') != settings.line_new_bot_user_id:
                raise CheckFailure('wrong_line_account')
            add('line_identity', 'pass')
            webhook = await metadata(client, 'https://api.line.me/v2/bot/channel/webhook/endpoint', headers)
            endpoint = urlsplit(webhook.get('endpoint', ''))
            valid = (webhook.get('active') is True and endpoint.scheme == 'https' and bool(endpoint.hostname)
                     and endpoint.path == '/webhook/line' and not endpoint.query and not endpoint.fragment
                     and not endpoint.username and not endpoint.password)
            add('line_webhook', 'pass' if valid else 'fail', 'delivery_not_verified' if valid else 'webhook_not_ready')
        except CheckFailure as error: add('line_identity_or_webhook', 'fail', error.reason)
        except (TypeError, ValueError): add('line_webhook', 'fail', 'invalid_response')
    return finish(checks, local_only)


def model_names(value):
    rows = value.get('models')
    if not isinstance(rows, list): raise CheckFailure('invalid_response')
    names = set()
    for row in rows:
        if not isinstance(row, dict): raise CheckFailure('invalid_response')
        name = row.get('name', row.get('model'))
        if not isinstance(name, str): raise CheckFailure('invalid_response')
        names.add(name)
        if name.endswith(':latest'): names.add(name[:-7])
    return names


def finish(checks, local_only):
    status = ('needs_attention' if any(c['status']=='fail' for c in checks) else
              'checks_passed_with_warnings' if any(c['status'] in {'warn','skipped'} for c in checks) else
              'checks_passed')
    return {
        'status': status,
        'scope': 'read_only_metadata_only', 'cloud_metadata_checked': not local_only,
        'inference_verified': False, 'line_delivery_verified': False, 'indexed_content_verified': False,
        'checks': checks,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-only', action='store_true', help='Skip OpenRouter and LINE metadata requests')
    parser.add_argument('--json', action='store_true', help='Print only sanitized machine-readable JSON')
    args = parser.parse_args()
    async def run():
        async with httpx.AsyncClient() as client:
            return await check_readiness(Settings(), client, local_only=args.local_only)
    try:
        report = asyncio.run(run())
    except Exception:
        # Config/HTTP/SQLite exception strings can contain credentials or paths.
        report = {'status': 'needs_attention', 'scope': 'read_only_metadata_only',
                  'checks': [{'check': 'configuration', 'status': 'fail', 'reason': 'configuration_or_check_failed'}]}
    if args.json:
        print(json.dumps(report, ensure_ascii=True))
    else:
        print('UNNAMED readiness check (read-only; no AI inference or LINE messages)')
        for row in report['checks']:
            print(f"[{row['status'].upper()}] {row['check']}: {row['reason']}")
            if row['check'] == 'local_budgets':
                for role, value in row.get('roles', {}).items():
                    print(f"  {role}: day {value['day_used']}/{value['day_limit']}, minute {value['minute_used']}/{value['minute_limit']}")
            if row['check'] == 'openrouter_key' and 'remaining_usd' in row:
                print(f"  key limit ${row.get('limit_usd')}; remaining ${row['remaining_usd']}")
            if row['check'] == 'lightrag_documents' and 'processed' in row:
                print(f"  processed {row['processed']}, expected {row['expected_count']}, unfinished {row['unfinished']}")
        print('Status:', report['status'])
        print('Model responses, exact indexed content and real LINE delivery remain unverified.')
    raise SystemExit(1 if report['status']=='needs_attention' else 0)


if __name__ == '__main__': main()
