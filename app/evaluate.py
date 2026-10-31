"""Evaluate synthetic questions only; never save transcripts or credentials."""
import argparse
import asyncio
import json
import math
import tempfile
import time
from pathlib import Path
import httpx
from .config import ROOT, Settings
from .engine import Engine
from .knowledge import Knowledge
from .policy import MESSAGES
from .providers import Providers
from .storage import Store


class CountingProviders(Providers):
    def __init__(self, *args):
        super().__init__(*args)
        self.calls = {'guard': 0, 'retrieval': 0, 'planner': 0, 'vision': 0}

    async def moderate(self, *args, **kwargs):
        self.calls['guard'] += 1
        return await super().moderate(*args, **kwargs)

    async def retrieve(self, *args, **kwargs):
        self.calls['retrieval'] += 1
        return await super().retrieve(*args, **kwargs)

    async def completion(self, role, *args, **kwargs):
        self.calls[role] += 1
        return await super().completion(role, *args, **kwargs)


async def evaluate(settings, cases):
    # Evaluation context/budgets use a temporary DB; production state stays untouched.
    cache = ROOT / '.cache'
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='evaluation-', dir=cache) as temporary:
        s = settings.model_copy(update={'data_dir': Path(temporary)})
        kb, store = Knowledge(s.knowledge_dir), Store(s)
        async with httpx.AsyncClient() as client:
            providers = CountingProviders(s, kb, store, client)
            engine = Engine(s, kb, store, providers)
            rows, latencies = [], []
            for case in cases:
                owner = store.key('evaluation', case['id'])
                began = time.perf_counter()
                before = providers.calls.copy()
                answer = None
                for message in case['messages']:
                    async with asyncio.timeout(40):
                        answer = await engine.answer(message, owner)
                elapsed = round((time.perf_counter() - began) * 1000, 1)
                latencies.append(elapsed)
                expected_ids = set(case.get('evidence', []))
                evidence_ok = expected_ids <= set(answer.evidence_ids)
                contains_ok = all(text in answer.reply for text in case.get('contains', []))
                outcome = case.get('outcome')
                outcome_ok = not outcome or answer.reply == MESSAGES[outcome]
                rows.append({'id': case['id'], 'category': case['category'],
                             'passed': evidence_ok and contains_ok and outcome_ok,
                             'expected_evidence': sorted(expected_ids), 'actual_evidence': answer.evidence_ids,
                             'evidence_ok': evidence_ok, 'outcome_ok': outcome_ok, 'contains_ok': contains_ok,
                             'blocked': answer.reply == MESSAGES['blocked'],
                             'unavailable': answer.reply == MESSAGES['unavailable'],
                             'latency_ms': elapsed,
                             'calls': {k: providers.calls[k] - before[k] for k in before}})
                if s.bot_mode == 'connected':
                    # Pace cloud roles below 15/minute; text Guard is called twice per answer.
                    await asyncio.sleep(10)
            ordered = sorted(latencies)
            categories = sorted({r['category'] for r in rows})
            return {'mode': s.bot_mode, 'scope': 'synthetic text cases; not an image or live LINE evaluation',
                    'total': len(rows), 'passed': sum(r['passed'] for r in rows),
                    'categories': {c: {'total': sum(r['category'] == c for r in rows),
                                      'passed': sum(r['category'] == c and r['passed'] for r in rows)} for c in categories},
                    'evidence_recall_cases': {'total': sum(bool(r['expected_evidence']) for r in rows),
                                            'passed': sum(bool(r['expected_evidence']) and r['evidence_ok'] for r in rows)},
                    'false_blocks': sum(r['blocked'] and r['category'] not in {'injection', 'privacy'} for r in rows),
                    'unavailable': sum(r['unavailable'] for r in rows), 'provider_attempts': providers.calls,
                    'latency_ms': {'p50': ordered[len(ordered) // 2], 'p95': ordered[math.ceil(len(ordered) * .95) - 1]},
                    'results': rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--connected', action='store_true', help='Explicitly allow configured external AI calls')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'evaluation-demo.json')
    args = parser.parse_args()
    try:
        s = Settings()
        if args.connected and s.bot_mode != 'connected':
            parser.error('Set BOT_MODE=connected and complete configuration first')
        if not args.connected:
            s = s.model_copy(update={'bot_mode': 'demo', 'enable_images': False})
        cases = json.loads((ROOT / 'evaluation' / 'cases.json').read_text(encoding='utf-8'))
        if args.limit: cases = cases[:max(1, args.limit)]
        report = asyncio.run(evaluate(s, cases))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: v for k, v in report.items() if k != 'results'}, ensure_ascii=False))
        raise SystemExit(0 if report['passed'] == report['total'] else 1)
    except Exception:
        parser.exit(1, 'Evaluation failed. Check settings/readiness; no credentials were printed.\n')


if __name__ == '__main__': main()
