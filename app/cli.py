import argparse
import asyncio
import json
import secrets
from pathlib import Path
import httpx
from cryptography.fernet import Fernet
from .config import ROOT, Settings
from .knowledge import Knowledge


def initialize(path=ROOT / '.env'):
    template = (ROOT / '.env.example').read_text(encoding='utf-8')
    for key, value in [('BOT_ADMIN_KEY', secrets.token_urlsafe(32)), ('DATA_KEY', Fernet.generate_key().decode()),
                       ('IDENTITY_HASH_KEY', secrets.token_urlsafe(32))]:
        template = template.replace(f'{key}=\n', f'{key}={value}\n')
    with path.open('x', encoding='utf-8') as handle:
        handle.write(template)
    # Never print credentials.
    print('Created private .env. Existing files are never overwritten.')


async def ingest(settings, kb):
    if not settings.lightrag_api_key:
        raise ValueError('Configure LIGHTRAG_API_KEY first')
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        for doc in kb.documents().values():
            response = await client.post(settings.lightrag_url.rstrip('/') + '/documents/text',
                headers={'X-API-Key': settings.lightrag_api_key},
                json={'text': doc.indexed_text, 'file_source': kb.file_source(doc)})
            if response.status_code == 409:
                print(f'{doc.id}: already exists; confirm index status in LightRAG')
                continue
            if response.status_code != 200:
                raise ValueError('Ingestion failed; inspect LightRAG readiness and configuration')
            result = response.json()
            if result.get('status') != 'success': raise ValueError('Ingestion was not accepted')
            print(f'{doc.id}: accepted (indexing continues in LightRAG)')
    print('Wait for all documents to finish indexing in LightRAG before querying.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['init', 'check', 'export', 'ingest'])
    args = parser.parse_args()
    try:
        if args.command == 'init':
            initialize()
            return
        s = Settings()
        kb = Knowledge(s.knowledge_dir)
        if args.command == 'check':
            print(json.dumps({'mode': s.bot_mode, 'services': len(kb.store['services']), 'documents': len(kb.documents()),
                              'revision': kb.revision(), 'local_keys_ready': all([s.bot_admin_key, s.data_key, s.identity_hash_key]),
                              'images_enabled': s.enable_images, 'live_connections_verified': False}))
        elif args.command == 'export':
            target = s.data_dir / 'public-export' / kb.revision()
            target.mkdir(parents=True, exist_ok=True)
            for doc in kb.documents().values(): (target / f'{doc.id}.txt').write_text(doc.indexed_text, encoding='utf-8')
            print(f'Exported {len(kb.documents())} public documents to {target}')
        else:
            asyncio.run(ingest(s, kb))
    except FileExistsError:
        parser.exit(1, '.env already exists; refusing to replace keys.\n')
    except Exception:
        # Settings validation errors can embed values: do not print exception text.
        parser.exit(1, 'Configuration/action failed. Check local settings and service readiness.\n')


if __name__ == '__main__': main()
