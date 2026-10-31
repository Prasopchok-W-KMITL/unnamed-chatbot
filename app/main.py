import asyncio
import contextlib
import json
import secrets
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .config import Settings
from .knowledge import Knowledge
from .storage import Store
from .providers import Providers
from .engine import Engine
from .line import Line
from .worker import Worker
from .schemas import ChatRequest
from .policy import MESSAGES
from .diagnostics import diagnostic_file, request_trace, decide


def create_app(settings=None, *, client=None, start_worker=True):
    try:
        s = settings or Settings()
    except Exception:
        # Pydantic errors may embed secret environment values in startup logs.
        raise RuntimeError('Invalid chatbot configuration; check local .env without sharing credentials.') from None
    @asynccontextmanager
    async def lifespan(app):
        with diagnostic_file(s):
            kb = Knowledge(s.knowledge_dir)
            store = Store(s)
            http = client or httpx.AsyncClient()
            providers = Providers(s, kb, store, http)
            engine, line = Engine(s, kb, store, providers), Line(s, providers)
            worker = Worker(store, engine, line)
            app.state.store, app.state.engine, app.state.line, app.state.worker = store, engine, line, worker
            task = asyncio.create_task(worker.run()) if start_worker and s.bot_mode == 'connected' else None
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError): await task
                if client is None: await http.aclose()

    app = FastAPI(title='UNNAMED isolated chatbot', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def limits(request, call_next):
        maximum = 65536 if request.url.path == '/webhook/line' else 8192
        try:
            if int(request.headers.get('content-length', '0')) > maximum:
                return JSONResponse({'error': 'payload_too_large'}, status_code=413)
        except ValueError:
            return JSONResponse({'error': 'invalid_length'}, status_code=400)
        if request.method == 'POST':
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > maximum: return JSONResponse({'error': 'payload_too_large'}, status_code=413)
            request._body = bytes(data)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({'error': 'invalid_request'}, status_code=422)

    def authorize(request):
        given = request.headers.get('x-admin-key', '')
        if not s.bot_admin_key or not secrets.compare_digest(given.encode(), s.bot_admin_key.encode()):
            raise HTTPException(401, 'unauthorized')

    @app.get('/healthz')
    async def health():
        return {'status': 'ok', 'mode': s.bot_mode}

    @app.get('/api/status')
    async def status(request: Request):
        authorize(request)
        return {'mode': s.bot_mode, 'services': len(app.state.engine.kb.store['services']),
                'documents': len(app.state.engine.kb.documents()), 'images_enabled': s.enable_images,
                'knowledge_revision': app.state.engine.kb.revision(),
                'diagnostics_enabled': s.diagnostics_enabled, 'diagnostics_version': 1,
                'external_connections_verified': False}

    @app.post('/api/chat')
    async def chat(body: ChatRequest, request: Request):
        authorize(request)
        session_id = body.session_id or secrets.token_urlsafe(24)
        owner = app.state.store.key('demo_session', session_id)
        with request_trace('api_text'):
            try:
                async with asyncio.timeout(40):
                    answer = await app.state.engine.answer(body.message, owner)
            except TimeoutError:
                decide('unavailable', 'timeout', stage='request')
                answer = app.state.engine.response(MESSAGES['unavailable'])
        answer.session_id = session_id
        return answer

    @app.post('/webhook/line')
    async def webhook(request: Request):
        if s.bot_mode != 'connected': raise HTTPException(503, 'connected_mode_required')
        if not request.headers.get('content-type', '').lower().startswith('application/json'): raise HTTPException(415, 'json_required')
        raw = await request.body()
        line = app.state.line
        if not line.signature_valid(raw, request.headers.get('x-line-signature')): raise HTTPException(403, 'invalid_signature')
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError): raise HTTPException(400, 'invalid_json')
        if not isinstance(data, dict) or not isinstance(data.get('events'), list) or len(data['events']) > 10: raise HTTPException(400, 'invalid_events')
        if data.get('destination') != s.line_new_bot_user_id: raise HTTPException(403, 'wrong_account')
        try: await line.verify_identity()
        except Exception: raise HTTPException(503, 'identity_unavailable') from None
        for event in data['events']:
            parsed = line.parse_event(event)
            if parsed:
                if app.state.store.enqueue(*parsed) == 'busy': raise HTTPException(503, 'queue_busy')
        return {'status': 'accepted'}

    return app


app = create_app()
