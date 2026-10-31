import asyncio
import logging
from .policy import MESSAGES
from .diagnostics import request_trace, span, decide, record, reason_for
from .safety import explicit_self_harm, simple_distress

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, store, engine, line):
        self.store, self.engine, self.line = store, engine, line

    async def once(self):
        job = self.store.claim()
        if not job: return False
        kind = job['payload']['kind']
        budget = min(40, max(0.1, job['expires']-self.store.clock()-5))
        with request_trace('line_' + kind if kind in {'text', 'image'} else 'line_other', budget_seconds=budget):
            record('stage_finished', stage='queue', duration_ms=max(0, self.store.clock()-job['created'])*1000)
            return await self.process(job)

    async def process(self, job):
        payload = job['payload']
        try:
            async with asyncio.timeout(min(40, max(0.1, job['expires'] - self.store.clock() - 5))):
                await self.show_loading(payload)
                if payload['kind'] == 'text':
                    answer = await self.engine.answer(payload['text'], job['owner'])
                    text = answer.reply
                elif payload['kind'] == 'image' and self.engine.s.enable_images:
                    with span('image_download'):
                        image = await self.line.download_image(payload['message_id'])
                    text = (await self.engine.image(image, job['owner'])).reply
                else:
                    text = MESSAGES.get(payload['kind'], MESSAGES['media'])
                    decide(payload['kind'] if payload['kind'] in {'greeting', 'length', 'rate', 'media'} else 'media')
        except Exception as error:
            record('failure', stage='worker', reason=reason_for(error))
            decide('unavailable', reason_for(error), stage='worker')
            text = MESSAGES['unavailable']
        if not self.store.delivering(job):
            decide('expired', 'job_expired', stage='worker')
            self.store.finish(job, 'failed')
            return True
        try:
            with span('line_reply'):
                await self.line.reply(payload['reply_token'], text)
            self.store.finish(job, 'done')
        except Exception:
            self.store.finish(job, 'uncertain')
            log.warning('line_delivery_uncertain')
            decide('uncertain', 'delivery_uncertain', stage='line_reply')
        return True

    async def show_loading(self, payload):
        kind = payload['kind']
        if (not self.engine.s.line_loading_enabled or not payload.get('loading_user_id')
                or kind not in {'text', 'image'}
                or (kind == 'image' and not self.engine.s.enable_images)):
            return
        if kind == 'text' and (explicit_self_harm(payload['text']) or simple_distress(payload['text'])):
            # A cosmetic request must not delay the fixed local support route.
            return
        with span('line_loading'):
            try:
                # Keep this bound even if a replacement Line adapter forgets
                # its own timeout. Failure must never block the actual answer.
                async with asyncio.timeout(1):
                    await self.line.loading(payload['loading_user_id'])
            except Exception:
                record('failure', stage='line_loading', reason='loading_unavailable')

    async def run(self):
        while True:
            try:
                if not await self.once(): await asyncio.sleep(0.25)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning('worker_cycle_failed')
                await asyncio.sleep(1)
