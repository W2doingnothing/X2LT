# SPDX-License-Identifier: GPL-2.0-only
"""Official Guangyapan OpenAPI OAuth and video-on-demand client."""
import base64
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
from secrets import compare_digest, token_urlsafe
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from .security import Vault


AUTH = 'https://openapi-account.guangyapan.com/v1/auth/token'
API = 'https://openapi.guangyapan.com/openapi/v1/'
LOGIN = 'https://www.guangyapan.com/oauth/'
DEFAULT_REDIRECT = 'http://127.0.0.1:17863/x2lt/guangyapan'


class CallbackServer(HTTPServer):
    allow_reuse_address = False


def playback_url(value):
    if not isinstance(value, str) or len(value) > 16384:
        raise ValueError('光鸭没有返回点播地址')
    parts = urlsplit(value)
    host = parts.hostname or ''
    if (parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443)
            or not (host.endswith('.guangyacdn.com') or host.endswith('.guangyapan.com'))):
        raise ValueError('光鸭返回的点播域名当前不受支持')
    return value


class GuangyapanPlayback:
    def __init__(self, data, open_url):
        self.vault = Vault(Path(data)/'guangyapan-open-auth'/'oauth.dpapi')
        self.open_url = open_url
        self.lock = threading.RLock()
        self.pending = None
        self.state = 'idle'
        self.session = {}
        self.bound = None
        try:
            self.session = self.vault.load()
        except (OSError, ValueError):
            pass

    def status(self):
        with self.lock:
            if self.pending and time.monotonic() >= self.pending['deadline']:
                self.pending = None
                self.state = 'expired'
            return dict(state=self.state, connected=bool(self.session.get('refresh_token')))

    def request(self, method, url, **kwargs):
        try:
            with requests.Session() as session:
                session.trust_env = False
                with session.request(method, url, timeout=(4, 10), allow_redirects=False,
                                     stream=True, **kwargs) as response:
                    if response.status_code in (401, 403):
                        return {'code': 117 if response.status_code == 401 else 123}
                    if response.status_code != 200:
                        raise ValueError('光鸭开放平台响应异常')
                    deadline = time.monotonic() + 15
                    content = bytearray()
                    for chunk in response.iter_content(16 * 1024):
                        content.extend(chunk)
                        if len(content) > 2 * 1024**2 or time.monotonic() > deadline:
                            raise ValueError('光鸭开放平台响应过大或超时')
                    payload = json.loads(content)
        except (requests.RequestException, ValueError):
            raise ValueError('光鸭开放平台暂时不可用') from None
        if not isinstance(payload, dict):
            raise ValueError('光鸭开放平台响应格式无效')
        return payload

    def start(self, client, redirect):
        if not re.fullmatch(r'[A-Za-z0-9_-]{3,120}', client) or client.startswith('dvlp_'):
            raise ValueError('请输入开放平台应用 ID，TOKEN 管理的开发者 ID 无法用于点播授权')
        parts = urlsplit(redirect)
        if (parts.scheme != 'http' or parts.hostname != '127.0.0.1' or parts.username or parts.password
                or not parts.port or not re.fullmatch(r'/[A-Za-z0-9_/-]+', parts.path) or parts.query or parts.fragment):
            raise ValueError('请使用已登记的本机回调地址，例如 ' + DEFAULT_REDIRECT)
        with self.lock:
            if self.pending and time.monotonic() < self.pending['deadline']:
                raise ValueError('光鸭授权正在等待浏览器回复')
            attempt = dict(client=client, redirect=redirect, state=token_urlsafe(32),
                           verifier=token_urlsafe(48), deadline=time.monotonic() + 180)
            handler = self.handler(attempt, parts.path)
            try:
                server = CallbackServer(('127.0.0.1', parts.port), handler)
            except OSError:
                raise ValueError('本机回调端口不可用，请检查登记的端口') from None
            server.timeout = 0.5
            self.pending, self.state = attempt, 'waiting'
            threading.Thread(target=self.listen, args=(server, attempt), daemon=True,
                             name='x2lt-guangyapan-oauth').start()
        challenge = base64.urlsafe_b64encode(sha256(attempt['verifier'].encode()).digest()).rstrip(b'=').decode()
        url = LOGIN + '?' + urlencode(dict(client_id=client, response_type='code', scope='user offline',
                           code_challenge=challenge, code_challenge_method='S256', redirect_uri=redirect,
                           state=attempt['state']))
        self.open_url(url)
        return self.status()

    def handler(self, attempt, callback_path):
        playback = self

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def setup(self):
                self.request.settimeout(3)
                super().setup()

            def do_GET(self):
                parts = urlsplit(self.path)
                if parts.path != callback_path or len(parts.query) > 4096:
                    self.send_error(404)
                    return
                fields = parse_qs(parts.query)
                state = fields.get('state', [])
                if len(state) != 1 or not state[0].isascii() or not compare_digest(state[0], attempt['state']):
                    self.send_error(400)
                    return
                playback.finish(attempt, fields)
                content = '可关闭此页面，在 X2LT 查看光鸭授权结果。'.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        return Callback

    def listen(self, server, attempt):
        with server:
            while self.status()['state'] == 'waiting' and self.pending is attempt:
                server.handle_request()

    def finish(self, attempt, fields):
        with self.lock:
            if self.pending is not attempt or time.monotonic() >= attempt['deadline']:
                return
            codes = fields.get('code', [])
            if fields.get('error') or len(codes) != 1:
                self.pending, self.state = None, 'cancelled'
                return
            try:
                session = self.tokens(attempt['client'], dict(grant_type='authorization_code', code=codes[0],
                                      code_verifier=attempt['verifier'], redirect_uri=attempt['redirect']))
                self.vault.save({'client': session['client'], 'refresh_token': session['refresh_token']})
                self.session, self.bound = session, None
                self.state = 'connected'
            except (OSError, ValueError, KeyError):
                self.state = 'failed'
            finally:
                self.pending = None

    def close(self):
        with self.lock:
            self.pending = None
            self.state = 'idle'

    def tokens(self, client, grant):
        payload = self.request('POST', AUTH, headers={'x-client-id': client}, json={'client_id': client, **grant})
        data = payload.get('data', payload)
        if not isinstance(data, dict) or payload.get('code', 0) != 0:
            raise ValueError('光鸭未接受本次授权，请重新连接点播')
        access = data.get('access_token')
        refresh = data.get('refresh_token') or grant.get('refresh_token')
        expiry = data.get('expires_in')
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh or type(expiry) is not int or expiry <= 0:
            raise ValueError('光鸭未返回有效点播凭证')
        return dict(client=client, access_token=access, refresh_token=refresh, expires=time.time() + expiry)

    def business(self, endpoint, params=None):
        if not self.session.get('refresh_token'):
            raise ValueError('请先连接在线播放')
        for attempt in range(2):
            if time.time() + 15 >= self.session.get('expires', 0):
                try:
                    self.session = self.tokens(self.session['client'], dict(grant_type='refresh_token',
                                               refresh_token=self.session['refresh_token']))
                    self.vault.save({'client': self.session['client'], 'refresh_token': self.session['refresh_token']})
                except (ValueError, OSError, KeyError):
                    raise ValueError('点播授权不可用，请重新连接在线播放') from None
            headers = {'x-client-id': self.session['client'], 'Authorization': 'Bearer ' + self.session['access_token']}
            result = self.request('GET', API + endpoint, headers=headers, params=params)
            code = result.get('code')
            if code == 0 and isinstance(result.get('data'), dict):
                return result['data']
            if code == 117 and attempt == 0:
                self.session['expires'] = 0
                continue
            messages = {111: '光鸭拒绝访问此媒体', 123: '此应用未开通光鸭点播能力',
                        146: '光鸭文件已不存在', 164: '此画质需要光鸭会员',
                        167: '此媒体不可播放', 430: '当前账号没有有效的光鸭会员'}
            raise ValueError(messages.get(code, '光鸭未能提供点播服务'))

    def video(self, remote, account, provider, previous=None):
        with self.lock:
            if self.bound != account:
                info = self.business('user/get_user_info')
                if str(info.get('userId', '')) != str(account):
                    root = self.business('file/get_file_list', {'parentId': '', 'pageSize': 100})
                    ids = {item.get('fileId') for item in root.get('list', [])}
                    local = {item['id'] for item in provider.list('', 0)['items']}
                    if not ids.intersection(local):
                        raise ValueError('点播授权与当前云盘账号不一致')
                self.bound = account
            if previous:
                biz, request = previous
            else:
                detail = self.business('file/get_file_detail', {'fileId': remote})
                variants = [r for r in detail.get('videoResource', []) if r.get('bizId')]
                if not variants:
                    raise ValueError('光鸭没有提供此视频的点播版本')
                preferred = next((r for r in variants if (r.get('info') or {}).get('defaultResolution')), variants[0])
                biz, request = preferred['bizId'], None
            params = {'fileId': remote, 'bizId': biz}
            if request:
                params['requestId'] = request
            result = self.business('file/get_vod_download_url', params)
            url = playback_url(result.get('signedURL'))
            return url, (biz, result.get('requestId') or request)
