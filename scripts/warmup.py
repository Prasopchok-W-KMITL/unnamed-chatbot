"""Preload this project's local Ollama models before a connected demo."""
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import ROOT, Settings


async def main():
    s = Settings()
    if s.bot_mode != 'connected':
        print('Demo mode: local model warmup skipped.')
        return
    rag = dotenv_values(ROOT / '.env.lightrag')
    if s.guard_base_url.rstrip('/') != 'http://127.0.0.1:11434/v1' or rag.get('LLM_BINDING') != 'ollama' or rag.get('EMBEDDING_BINDING') != 'ollama':
        raise ValueError('This helper requires the documented local Ollama setup.')
    requests = [
        ('guard', '/api/chat', {'model': s.guard_model, 'messages': [{'role': 'user', 'content': 'ร้านเปิดกี่โมงครับ'}],
            'stream': False, 'keep_alive': -1, 'options': {'temperature': 0, 'num_predict': 80}}),
        ('rag_llm', '/api/chat', {'model': rag['LLM_MODEL'], 'messages': [{'role': 'user', 'content': 'สวัสดี'}],
            'stream': False, 'keep_alive': -1, 'options': {'temperature': 0, 'num_predict': 1, 'num_ctx': int(rag.get('OLLAMA_LLM_NUM_CTX', '8192'))}}),
        ('embedding', '/api/embed', {'model': rag['EMBEDDING_MODEL'], 'input': 'ข้อมูลร้านทำผม',
            'keep_alive': -1, 'options': {'num_ctx': int(rag.get('OLLAMA_EMBEDDING_NUM_CTX', '8192'))}}),
    ]
    async with httpx.AsyncClient(timeout=180, follow_redirects=False) as client:
        for role, endpoint, payload in requests:
            start = time.monotonic()
            r = await client.post('http://127.0.0.1:11434' + endpoint, json=payload)
            r.raise_for_status()
            value = r.json()
            if role == 'guard' and value.get('message', {}).get('content', '').strip() != 'safe':
                raise ValueError('Guard warmup did not return safe.')
            if role == 'embedding' and len(value.get('embeddings', [[]])[0]) != int(rag['EMBEDDING_DIM']):
                raise ValueError('Embedding dimension mismatch.')
            print(json.dumps({'ready': role, 'seconds': round(time.monotonic() - start, 2)}), flush=True)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception:
        # Never print settings, response bodies or credential-bearing errors.
        sys.exit('Warmup failed. Check Ollama, downloaded models and local configuration.')
