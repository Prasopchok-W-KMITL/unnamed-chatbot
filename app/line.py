import base64
import asyncio
import hashlib
import hmac
import re
import time
from .providers import ProviderUnavailable


class Line:
    def __init__(self, settings, providers):
        self.s, self.p = settings, providers
        self.identity_until = 0

    def signature_valid(self, raw, signature):
        if not self.s.line_new_channel_secret or not isinstance(signature, str) or not re.fullmatch(r'[A-Za-z0-9+/]{43}=', signature):
            return False
        expected = base64.b64encode(hmac.new(self.s.line_new_channel_secret.encode(), raw, hashlib.sha256).digest()).decode()
        return hmac.compare_digest(expected, signature)

    @property
    def headers(self):
        return {'Authorization': 'Bearer ' + self.s.line_new_channel_access_token}

    async def verify_identity(self):
        if self.identity_until > time.time():
            return
        result = await self.p.json_request('GET', 'https://api.line.me/v2/bot/info', headers=self.headers, timeout=4)
        if result.get('userId') != self.s.line_new_bot_user_id:
            raise ProviderUnavailable()
        self.identity_until = time.time() + 300

    def parse_event(self, event, now=None):
        if not isinstance(event, dict): return None
        now = time.time() if now is None else now
        source = event.get('source')
        if not isinstance(source, dict) or source.get('type') != 'user' or not re.fullmatch(r'U[a-f0-9]{32}', str(source.get('userId', ''))): return None
        if not isinstance(event.get('webhookEventId'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', event['webhookEventId']): return None
        token = event.get('replyToken')
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', token) or token == '0' * 32: return None
        timestamp = event.get('timestamp')
        if type(timestamp) is not int or not (now - 120 <= timestamp / 1000 <= now + 60): return None
        payload = {'reply_token': token}
        if event.get('type') == 'follow':
            payload['kind'] = 'greeting'
        elif event.get('type') == 'message' and isinstance(event.get('message'), dict):
            message = event['message']
            if message.get('type') == 'text' and isinstance(message.get('text'), str):
                payload.update(kind='text', text=message['text']) if len(message['text']) <= 1000 else payload.update(kind='length')
            elif message.get('type') == 'image' and isinstance(message.get('id'), str) and re.fullmatch(r'\d{1,64}', message['id']):
                payload.update(kind='image', message_id=message['id'])
            else:
                payload['kind'] = 'media'
        else: return None
        if self.s.line_loading_enabled and (payload['kind'] == 'text' or
                                           (payload['kind'] == 'image' and self.s.enable_images)):
            # This ID exists only in the encrypted short-lived queue payload;
            # it is never added to a profile, diagnostic trace or AI request.
            payload['loading_user_id'] = source['userId']
        return event['webhookEventId'], source['userId'], payload

    async def loading(self, user_id):
        """Best-effort cosmetic request; never retry or consume a reply token."""
        if not self.s.line_loading_enabled:
            return False
        seconds = self.s.line_loading_seconds
        if (not isinstance(user_id, str) or not re.fullmatch(r'U[a-f0-9]{32}', user_id)
                or not self.s.line_new_channel_access_token or type(seconds) is not int
                or not 5 <= seconds <= 60 or seconds % 5):
            raise ProviderUnavailable('configuration')
        # LINE specifies HTTP 202 rather than the 200 required by json_request.
        # The hard total timeout also bounds slow/drip responses. Do not read
        # provider bodies or pass this side effect through inference retries.
        async with asyncio.timeout(1):
            async with self.p.client.stream('POST', 'https://api.line.me/v2/bot/chat/loading/start',
                    headers=self.headers, timeout=1, follow_redirects=False,
                    json={'chatId': user_id, 'loadingSeconds': seconds}) as response:
                if response.status_code != 202:
                    raise ProviderUnavailable('http_error')
        return True

    async def download_image(self, message_id):
        if not isinstance(message_id, str) or not re.fullmatch(r'\d{1,64}', message_id): raise ProviderUnavailable()
        try:
            async with self.p.client.stream('GET', f'https://api-data.line.me/v2/bot/message/{message_id}/content',
                                           headers=self.headers, timeout=5, follow_redirects=False) as response:
                if response.status_code != 200: raise ProviderUnavailable()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > self.s.max_image_bytes: raise ProviderUnavailable()
                return bytes(data)
        except Exception:
            raise ProviderUnavailable() from None

    async def reply(self, token, text):
        if not self.s.line_new_channel_access_token: raise ProviderUnavailable()
        # LINE counts UTF-16 code units. Bound each message conservatively.
        messages, part, units = [], '', 0
        for char in text:
            size = 2 if ord(char) > 0xffff else 1
            if units + size > 4500:
                messages.append({'type': 'text', 'text': part})
                part, units = '', 0
            part += char
            units += size
        if part: messages.append({'type': 'text', 'text': part})
        if not messages or len(messages) > 5: raise ProviderUnavailable()
        await self.p.json_request('POST', 'https://api.line.me/v2/bot/message/reply', timeout=4, headers=self.headers,
                                  json={'replyToken': token, 'messages': messages})
