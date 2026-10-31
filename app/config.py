from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')
    bot_mode: str = 'demo'
    bot_admin_key: str = ''
    data_key: str = ''
    identity_hash_key: str = ''
    data_dir: Path = ROOT / 'data'
    knowledge_dir: Path = ROOT / 'knowledge'
    line_new_channel_secret: str = ''
    line_new_channel_access_token: str = ''
    line_new_bot_user_id: str = ''
    openrouter_api_key: str = ''
    openrouter_model: str = ''
    openrouter_vision_model: str = ''
    openrouter_zdr: bool = True
    openrouter_disable_reasoning: bool = False
    openrouter_send_temperature: bool = True
    openrouter_planner_format: Literal['json_schema', 'json_object'] = 'json_schema'
    planner_timeout_seconds: int = Field(default=8, ge=1, le=20)
    retrieval_timeout_seconds: int = Field(default=20, ge=1, le=25)
    provider_max_retries: int = Field(default=1, ge=0, le=1)
    provider_retry_max_wait_seconds: float = Field(default=2, ge=0.1, le=5)
    allow_paid_models: bool = False
    lightrag_url: str = 'http://127.0.0.1:9621'
    lightrag_api_key: str = ''
    lightrag_mode: str = 'mix'
    guard_base_url: str = 'http://127.0.0.1:1234/v1'
    guard_api_key: str = ''
    guard_model: str = ''
    image_guard_provider: Literal['llama_guard', 'openai'] = 'llama_guard'
    image_guard_model: str = ''
    openai_moderation_api_key: str = ''
    enable_images: bool = False
    session_ttl_seconds: int = Field(default=1800, ge=60, le=86400)
    image_context_ttl_seconds: int = Field(default=300, ge=30, le=1800)
    model_calls_per_day: int = Field(default=40, ge=1, le=10000)
    retrieval_calls_per_day: int = Field(default=1000, ge=1, le=100000)
    retrieval_calls_per_minute: int = Field(default=30, ge=1, le=300)
    guard_calls_per_day: int = Field(default=1000, ge=1, le=100000)
    guard_calls_per_minute: int = Field(default=30, ge=1, le=300)
    max_image_bytes: int = Field(default=5_000_000, ge=1024, le=10_000_000)
    diagnostics_enabled: bool = True
    line_loading_enabled: bool = True
    line_loading_seconds: int = Field(default=40, ge=5, le=60, multiple_of=5)

    @model_validator(mode='after')
    def validate_config(self):
        if self.bot_mode not in {'demo', 'connected'}:
            raise ValueError('BOT_MODE must be demo or connected')
        if self.lightrag_mode not in {'naive', 'hybrid', 'mix', 'local', 'global'}:
            raise ValueError('Invalid LightRAG mode')
        for value in (self.lightrag_url, self.guard_base_url):
            parsed = urlparse(value)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Invalid service URL')
            if parsed.scheme == 'http' and parsed.hostname not in {'localhost', '127.0.0.1', 'lightrag', 'guard', 'host.docker.internal'}:
                raise ValueError('Use HTTPS for remote services')
        for name in ('bot_admin_key', 'identity_hash_key'):
            if getattr(self, name) and len(getattr(self, name)) < 32:
                raise ValueError('Keys must contain at least 32 characters')
        if self.data_key:
            Fernet(self.data_key.encode())
        for model in (self.openrouter_model, self.openrouter_vision_model):
            if model and not self.allow_paid_models and not (model.endswith(':free') or model == 'openrouter/free'):
                raise ValueError('Paid models require explicit ALLOW_PAID_MODELS=true')
        if self.bot_mode == 'connected':
            required = ['bot_admin_key', 'data_key', 'identity_hash_key', 'line_new_channel_secret',
                        'line_new_channel_access_token', 'line_new_bot_user_id', 'openrouter_api_key',
                        'openrouter_model', 'lightrag_api_key', 'guard_model']
            if any(not getattr(self, field) for field in required):
                raise ValueError('Connected mode configuration is incomplete')
            import re
            if not re.fullmatch(r'U[0-9a-f]{32}', self.line_new_bot_user_id):
                raise ValueError('Invalid NEW bot user ID')
        if self.enable_images and (self.bot_mode != 'connected' or not self.openrouter_vision_model or not self.image_guard_model):
            raise ValueError('Images require connected mode, Vision and an image-capable guard')
        if self.image_guard_provider == 'openai':
            if self.image_guard_model and self.image_guard_model not in {'omni-moderation-latest', 'omni-moderation-2024-09-26'}:
                raise ValueError('OpenAI image moderation requires an omni-moderation model')
            if self.enable_images and not self.openai_moderation_api_key:
                raise ValueError('OpenAI image moderation requires its own API key')
            if self.enable_images and self.openai_moderation_api_key.startswith('sk-or-'):
                raise ValueError('OpenAI image moderation cannot use an OpenRouter key')
        return self
