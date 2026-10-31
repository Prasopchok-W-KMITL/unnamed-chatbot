import json
import httpx
import pytest
from cryptography.fernet import Fernet
from app.config import ROOT, Settings
from app.knowledge import Knowledge
from app.storage import Store
from app.providers import Providers
from app.engine import Engine
from app.schemas import Decision
from app.safety import GuardVerdict


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, bot_admin_key='a' * 40, data_key=Fernet.generate_key().decode(),
                    identity_hash_key='h' * 40, data_dir=tmp_path, knowledge_dir=ROOT / 'knowledge')


@pytest.fixture
def kb(settings): return Knowledge(settings.knowledge_dir)


@pytest.fixture
def store(settings): return Store(settings)


class FakeProviders:
    def __init__(self, kb):
        self.kb, self.calls = kb, []
        self.safe, self.decision = True, Decision(intent='unknown')
        self.docs = None
        self.failure = False
        self.summary = None
        self.categories = frozenset()

    async def classify(self, text, **kwargs):
        self.calls.append(('guard', text, kwargs))
        if self.failure: raise RuntimeError('private provider error')
        return GuardVerdict(self.safe, self.categories)

    async def moderate(self, text, **kwargs):
        return (await self.classify(text, **kwargs)).safe

    async def retrieve(self, question):
        self.calls.append(('retrieve', question))
        return self.docs if self.docs is not None else list(self.kb.documents().values())

    async def decide(self, question, profile, documents):
        self.calls.append(('decide', question, profile.model_dump()))
        return self.decision

    async def describe_image(self, image): return self.summary


@pytest.fixture
def fake(kb): return FakeProviders(kb)


@pytest.fixture
def engine(settings, kb, store, fake): return Engine(settings, kb, store, fake)


def connected(settings):
    return settings.model_copy(update={'bot_mode': 'connected', 'line_new_channel_secret': 'new-secret',
        'line_new_channel_access_token': 'new-token', 'line_new_bot_user_id': 'U' + 'b' * 32,
        'openrouter_api_key': 'new-router-key', 'openrouter_model': 'google/gemma-3-27b-it:free',
        'lightrag_api_key': 'new-rag-key', 'guard_model': 'llama-guard'})
