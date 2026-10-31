import logging
import json
import re
from .schemas import Answer, Decision, Profile
from .policy import MESSAGES, normalize, filtered, local_profile, consultation, lexical_ids
from .safety import SELF_HARM_REPLY, DISTRESS_REPLY, explicit_self_harm, simple_distress
from .diagnostics import current, traced, span, decide, record, reason_for
from .errors import EvidenceError

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, settings, kb, store, providers):
        self.s, self.kb, self.store, self.providers = settings, kb, store, providers

    def response(self, text, **kwargs):
        trace = current.get()
        if trace:
            for name, value in MESSAGES.items():
                if text == value and name in {'unknown', 'blocked', 'unavailable', 'greeting', 'rate', 'media',
                                              'length', 'assessment', 'reset', 'image_unclear', 'image_received', 'image_expired'}:
                    trace.outcome = name
                    break
            if kwargs.get('safety_route', 'none') != 'none': trace.outcome = 'support'
        return Answer(reply=text, mode=self.s.bot_mode, request_id=trace.request_id if trace else '', **kwargs)

    def verified_promotion_reply(self, decision, documents, reply, missing):
        # Narrow S6 exception for exact copies of owner-approved public offers.
        # No generated prose, user text, guidance, or stale retrieval can qualify.
        if decision.intent != 'facts' or decision.guidance_ids or not decision.fact_ids:
            return False
        ids = decision.fact_ids
        if len(set(ids)) != len(ids):
            return False
        canonical = self.kb.documents()
        retrieved = {d.id: d for d in documents}
        approved = {s['id'] for s in self.kb.store['services'] if s.get('category') == 'promotion'}
        if any(i not in approved or i not in canonical or retrieved.get(i) != canonical[i] for i in ids):
            return False
        expected = 'ข้อมูลจากฐานความรู้ร้าน UNNAMED ครับ\n\n' + '\n\n'.join(canonical[i].text for i in ids)
        if missing:
            expected += '\n\nยังไม่มีข้อมูลยืนยันสำหรับบางหัวข้อที่ถามเพิ่มเติมครับ'
        return reply == expected

    def verified_kids_haircut_reply(self, decision, documents, reply, missing):
        if decision.intent != 'facts' or decision.guidance_ids or decision.fact_ids != ['kids-haircut']:
            return False
        canonical = self.kb.documents().get('kids-haircut')
        retrieved = [d for d in documents if d.id == 'kids-haircut']
        if canonical is None or retrieved != [canonical]:
            return False
        expected = 'ข้อมูลจากฐานความรู้ร้าน UNNAMED ครับ\n\n' + canonical.text
        if missing:
            expected += '\n\nยังไม่มีข้อมูลยืนยันสำหรับบางหัวข้อที่ถามเพิ่มเติมครับ'
        return reply == expected

    def public_contact_guard_text(self, decision, documents, reply, missing):
        """Mask only strict public contact values in a verified canonical reply.

        The customer still receives the untouched reply. No unsafe verdict is
        overridden: Guard must accept the entire remaining response as safe.
        Unverified/mixed/generated text is checked verbatim.
        """
        ids = decision.fact_ids
        allowed = {'contact-phone', 'contact-map', 'opening-hours'}
        if (decision.intent != 'facts' or decision.guidance_ids or not ids
                or len(ids) != len(set(ids)) or not set(ids) <= allowed):
            return reply
        canonical = self.kb.documents()
        retrieved = {d.id: d for d in documents}
        if any(i not in canonical or retrieved.get(i) != canonical[i] for i in ids):
            return reply
        expected = 'ข้อมูลจากฐานความรู้ร้าน UNNAMED ครับ\n\n' + '\n\n'.join(canonical[i].text for i in ids)
        if missing:
            expected += '\n\nยังไม่มีข้อมูลยืนยันสำหรับบางหัวข้อที่ถามเพิ่มเติมครับ'
        if reply != expected:
            return reply
        masked = reply
        patterns = {
            'contact-phone': (r'เบอร์ติดต่อร้าน UNNAMED: (0[0-9]{2}-[0-9]{3}-[0-9]{4})', '[public shop telephone]'),
            'contact-map': (r'แผนที่ร้าน UNNAMED: (https://maps\.app\.goo\.gl/[A-Za-z0-9]+)', '[public shop Google Maps business listing]'),
        }
        for identity, (pattern, replacement) in patterns.items():
            if identity in ids:
                match = re.fullmatch(pattern, canonical[identity].text)
                if match is None:
                    return reply
                masked = masked.replace(canonical[identity].text,
                                        canonical[identity].text.replace(match[1], replacement))
        return masked

    def demo_decision(self, q, p):
        ids, missing = lexical_ids(q, self.kb)
        advice = consultation(q) or (p.goal != 'unknown' and not ids and not missing)
        if advice:
            gs = [d.id for d in self.kb.documents().values() if d.topic == 'guidance' and p.goal in d.goals][:2]
            return Decision(**p.decision_slots(), intent='recommend' if gs else 'clarify', guidance_ids=gs), missing
        return Decision(**p.decision_slots(), intent='facts' if ids else 'unknown', fact_ids=ids), missing

    def render(self, decision, documents, p):
        docs = {d.id: d for d in documents}
        if len(set(decision.fact_ids)) != len(decision.fact_ids) or len(set(decision.guidance_ids)) != len(decision.guidance_ids):
            raise EvidenceError()
        if any(i not in docs or docs[i].topic == 'guidance' for i in decision.fact_ids):
            raise EvidenceError()
        if any(i not in docs or docs[i].topic != 'guidance' or p.goal not in docs[i].goals or docs[i].service_id not in docs for i in decision.guidance_ids):
            raise EvidenceError()
        if decision.intent == 'facts':
            if not decision.fact_ids or decision.guidance_ids:
                raise EvidenceError()
            return 'ข้อมูลจากฐานความรู้ร้าน UNNAMED ครับ\n\n' + '\n\n'.join(docs[i].text for i in decision.fact_ids), decision.fact_ids
        if decision.fact_ids or (decision.intent != 'recommend' and decision.guidance_ids):
            raise EvidenceError()
        if decision.intent == 'unknown':
            return MESSAGES['unknown'], []
        if p.risk == 'assessment' or p.history == 'chemical':
            return MESSAGES['assessment'], []
        required = []
        if p.goal == 'unknown': required.append('desired-look')
        if p.history == 'unknown': required.append('hair-history')
        if required:
            return 'ขอข้อมูลเพิ่มก่อนแนะนำบริการครับ\n\n' + '\n'.join(self.kb.questions[i] for i in required), []
        if decision.intent != 'recommend' or not decision.guidance_ids:
            return MESSAGES['unknown'], []
        ids = []
        parts = ['คำแนะนำเบื้องต้นจากข้อมูลทั่วไปครับ ความเหมาะสมกับผมจริงต้องให้ช่างประเมินก่อน']
        for i in decision.guidance_ids:
            g = docs[i]
            parts.extend([g.text, docs[g.service_id].text])
            ids.extend([i, g.service_id])
        return '\n\n'.join(parts), ids

    @traced('text')
    async def answer(self, text, owner):
        if len(text) > 1000:
            decide('length', 'length_limit')
            return self.response(MESSAGES['length'])
        if explicit_self_harm(text):
            decide('support', 'local_self_harm')
            return self.response(SELF_HARM_REPLY, safety_route='local_self_harm')
        q = filtered(text)
        if q is None:
            decide('blocked', 'local_filter')
            return self.response(MESSAGES['blocked'])
        if not q:
            decide('unknown', 'empty_input')
            return self.response(MESSAGES['unknown'])
        if simple_distress(q):
            decide('support', 'local_distress')
            return self.response(DISTRESS_REPLY, safety_route='local_distress')
        if q in {'ล้างบริบท', 'เริ่มใหม่', 'reset'}:
            decide('reset', 'reset')
            self.store.reset(owner)
            return self.response(MESSAGES['reset'])
        if q in {'สวัสดี', 'สวัสดีครับ', 'สวัสดีค่ะ', 'hello', 'hi'}:
            decide('greeting', 'greeting')
            return self.response(MESSAGES['greeting'])
        with span('profile'):
            profile = self.store.get_profile(owner)
            local = local_profile(q, profile)
        connected = self.s.bot_mode == 'connected'
        stage = 'decision'
        image_context = None
        try:
            if connected:
                stage = 'guard_input'
                with span('guard_input'):
                    verdict = await self.providers.classify(q)
                if 'S11' in verdict.categories:
                    decide('support', 'guard_self_harm', stage=stage, categories=verdict.categories)
                    return self.response(SELF_HARM_REPLY, guard_checked=True, safety_route='guard_self_harm')
                if not verdict.safe:
                    decide('blocked', 'guard_unsafe', stage=stage, categories=verdict.categories)
                    return self.response(MESSAGES['blocked'], guard_checked=True)
            explicit_ids, missing_topics = lexical_ids(q, self.kb)
            image_reference = bool(re.search(r'ภาพนี้|รูปนี้|ทรงนี้|แบบนี้|จาก(?:รูป|ภาพ)|ใน(?:รูป|ภาพ)|(?:รูป|ภาพ)(?:ก่อน|เดิม|นั้น|เมื่อกี้|ที่ส่ง)|(?:this|that|previous|last) (?:image|photo|style)|like (?:this|the photo)', q))
            price_or_duration = bool(re.search(r'ราคา|กี่บาท|เท่าไหร่|เท่าไร|กี่นาที|กี่ชั่วโมง|ใช้เวลา|price|cost|duration|how much', q))
            public_facts = self.kb.documents()
            direct_shop_facts = ((explicit_ids and all(public_facts[i].topic not in {'service','promotion','guidance'} for i in explicit_ids))
                                 or bool(missing_topics)) and not consultation(q)
            catalog_question = bool(re.search(r'มีโปร|โปรทั้งหมด|โปรโมชั่นอะไร|โปรโมชันอะไร|บริการทั้งหมด|มีบริการอะไร|บริการอะไรบ้าง|all services|promotions|deals', q)) and not consultation(q)
            # A named price/duration or direct public shop fact is answerable
            # without the image; keep both active references and expiry notices.
            independent_facts = bool(explicit_ids and price_or_duration) or direct_shop_facts or catalog_question
            needs_image = image_reference and not independent_facts
            if needs_image or (not explicit_ids and consultation(q)):
                stage = 'image_context'
                with span('image_context'):
                    image_context, image_expired = self.store.get_image_context_state(owner)
                if needs_image and image_context is None and image_expired:
                    decide('image_expired', 'image_expired', stage='image_context')
                    return self.response(MESSAGES['image_expired'], guard_checked=connected)
                if needs_image and image_context is None:
                    decide('image_unclear', 'image_missing', stage='image_context')
                    return self.response(MESSAGES['image_missing'], guard_checked=connected)
            explicit_goal = local_profile(q, Profile()).goal
            if image_context and image_reference and explicit_goal == 'unknown':
                local.goal = {'straight': 'straight', 'wavy': 'curls', 'curly': 'curls', 'unknown': 'unknown'}[image_context.texture]
                if local.goal == 'unknown' and image_context.volume == 'high':
                    local.goal = 'volume'
            context_question = q
            if image_context:
                context_question += '\nผลวิเคราะห์ภาพอ้างอิงล่าสุด (ไม่ใช่ประวัติลูกค้า): ' + json.dumps(image_context.model_dump(), ensure_ascii=False)
            if connected:
                # Context is finite labels only. Never forward user identifiers/history transcripts.
                search = context_question + '\nเป้าหมายที่ลูกค้าระบุ: ' + {'unknown': 'ยังไม่ทราบ', 'volume': 'วอลลุ่ม ดัดผม', 'curls': 'ลอน ดัดผม', 'straight': 'ผมตรง ยืดผม', 'smooth': 'ลดชี้ฟู เคราติน'}[local.goal]
                requested, _ = lexical_ids(q, self.kb)
                canonical = self.kb.documents()
                if not image_context and any(canonical[i].topic == 'promotion' for i in requested):
                    search += '\nบริการที่เกี่ยวข้อง: ' + '; '.join(canonical[i].text.split('\n')[0] for i in requested)
                stage = 'retrieval'
                with span('retrieval'):
                    documents = await self.providers.retrieve(search)
                if not documents:
                    decide('unknown', 'no_verified_evidence', stage=stage)
                    return self.response(MESSAGES['unknown'], retrieval='lightrag', guard_checked=True)
                stage = 'planner'
                with span('planner'):
                    decision = await self.providers.decide(context_question, local, documents)
                missing = lexical_ids(q, self.kb)[1]
                requested, _ = lexical_ids(q, self.kb)
                canonical = self.kb.documents()
                # Exact offer/list queries use only complete, verified retrieval
                # evidence, so the planner cannot silently omit a package.
                if not image_context and not consultation(q) and any(canonical[i].topic == 'promotion' for i in requested):
                    if not set(requested) <= {d.id for d in documents}:
                        raise EvidenceError('incomplete_evidence')
                    decision = Decision(**local.decision_slots(), intent='facts', fact_ids=requested)
                elif not image_context and decision.intent == 'facts' and requested and not consultation(q):
                    retrieved = {d.id: d for d in documents}
                    # Reject invalid planner references before narrowing valid
                    # ones. A location question must not add phone/hours merely
                    # because those documents were retrieved alongside the map.
                    if (len(set(decision.fact_ids)) != len(decision.fact_ids)
                            or any(i not in retrieved or retrieved[i].topic == 'guidance' for i in decision.fact_ids)
                            or decision.guidance_ids
                            or not set(requested) <= retrieved.keys()):
                        raise EvidenceError()
                    decision = decision.model_copy(update={'fact_ids': requested})
            else:
                documents = list(self.kb.documents().values())
                decision, missing = self.demo_decision(q, local)
            p = local.model_copy()
            # A model cannot invent a negative chemical history or confirmed length.
            # These slots require explicit user statements recognized locally.
            for field in ('goal',):
                value = getattr(decision, field)
                if value != 'unknown': setattr(p, field, value)
            if explicit_goal != 'unknown':
                p.goal = explicit_goal
            if decision.risk == 'assessment' or local.risk == 'assessment': p.risk = 'assessment'
            # Locally detected risk cannot be erased by a model.
            if local.history == 'chemical': p.history = 'chemical'
            # Facts need no personal profiling. Advice cannot be selected through facts IDs.
            explicit_facts, missing_topics = lexical_ids(q, self.kb)
            image_facts = image_reference and explicit_facts and price_or_duration
            image_advice = image_context and image_reference and not image_facts
            if ((consultation(q) and not image_facts) or image_advice or (profile.goal != 'unknown' and not explicit_facts and not missing_topics)) and decision.intent == 'facts':
                decision = Decision(**p.decision_slots(), intent='clarify')
            # Missing personal details always defer advice. Do not try to
            # validate or render premature model-selected recommendations:
            # clarification uses only fixed questions, with no evidence IDs.
            if decision.intent in {'recommend', 'clarify'} and 'unknown' in (p.goal, p.history):
                decision = Decision(**p.decision_slots(), intent='clarify')
            # Some planners attach retrieved service IDs to a recommendation's
            # unused facts array. Discard only valid, unique fact references;
            # guidance + its service must still pass render's full checks.
            if decision.intent == 'recommend' and decision.fact_ids:
                retrieved = {d.id: d for d in documents}
                if len(set(decision.fact_ids)) == len(decision.fact_ids) and all(
                    i in retrieved and retrieved[i].topic != 'guidance' for i in decision.fact_ids
                ):
                    decision = decision.model_copy(update={'fact_ids': []})
            stage = 'render'
            rejected = set(local.declined_services)
            if decision.intent == 'recommend' and any(self.kb.documents().get(i) and self.kb.documents()[i].service_id in rejected for i in decision.guidance_ids):
                decision = Decision(**p.decision_slots(), intent='unknown')
            with span('render'):
                reply, evidence = self.render(decision, documents, p)
            decide(decision.intent, stage='render')
            if missing and evidence: reply += '\n\nยังไม่มีข้อมูลยืนยันสำหรับบางหัวข้อที่ถามเพิ่มเติมครับ'
            stage = 'guard_output'
            if connected:
                guard_text = self.public_contact_guard_text(decision, documents, reply, missing)
                if guard_text != reply:
                    record('decision', stage='guard_output', reason='verified_public_contact_masked')
                with span('guard_output'):
                    if self.verified_kids_haircut_reply(decision, documents, reply, missing):
                        output_verdict = await self.providers.classify(guard_text, output=True, output_context='kids_haircut')
                    else:
                        output_verdict = await self.providers.classify(guard_text, output=True)
                if not output_verdict.safe:
                    approved_s6 = (output_verdict.categories == frozenset({'S6'})
                                   and self.verified_promotion_reply(decision, documents, reply, missing))
                    if not approved_s6:
                        decide('blocked', 'guard_unsafe', stage=stage, categories=output_verdict.categories)
                        return self.response(MESSAGES['blocked'], retrieval='lightrag', model_used=True, guard_checked=True)
                    log.warning('output_guard_public_promotion_exception category=S6')
                    decide('facts', 'promotion_s6_exception', stage=stage, categories=output_verdict.categories)
            stage = 'store_profile'
            with span('store_profile'):
                self.store.set_profile(owner, p)
                if image_context:
                    self.store.clear_image_context(owner)
            return self.response(reply, retrieval='lightrag' if connected else 'local', model_used=connected,
                                 guard_checked=connected, evidence_ids=evidence)
        except Exception as error:
            # Fixed message only, never raw provider output or exception text.
            # Log no question, identifiers, profile, response or exception text.
            log.warning('answer_unavailable stage=%s error_type=%s', stage, type(error).__name__)
            record('failure', stage=stage, reason=reason_for(error), outcome='unavailable')
            return self.response(MESSAGES['unavailable'])

    @traced('image')
    async def image(self, image_bytes, owner):
        if not self.s.enable_images:
            decide('media', 'images_disabled')
            return self.response(MESSAGES['media'])
        try:
            # A newly submitted image supersedes the old reference even when
            # moderation/vision fails. Never reuse an unrelated previous image.
            self.store.clear_image_context(owner)
            summary = await self.providers.describe_image(image_bytes)
            if summary is None:
                decide('blocked', 'guard_unsafe', stage='image_guard')
                return self.response(MESSAGES['blocked'], guard_checked=True)
            if summary.kind != 'hairstyle':
                decide('image_unclear', 'image_unsupported', stage='vision')
                return self.response(MESSAGES['image_unclear'], guard_checked=True, model_used=True)
            with span('image_context'):
                self.store.set_image_context(owner, summary)
            decide('image_received', stage='image_context')
            return self.response(MESSAGES['image_received'], guard_checked=True, model_used=True)
        except Exception as error:
            trace = current.get()
            record('failure', stage=trace.failed_stage if trace and trace.failed_stage else 'vision',
                   reason=reason_for(error), outcome='unavailable')
            return self.response(MESSAGES['unavailable'])
