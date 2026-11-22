# SPDX-License-Identifier: GPL-2.0-only
"""Read-only developer API and durable raster cache for Guangyapan media."""
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import re
from secrets import token_hex
import threading
import time
from urllib.parse import urlsplit

import requests

from .previews import PreviewCache
from .security import Vault


DEVELOPER_URL = 'https://dapi.guangyapan.com/userres/v1/file/'


def thumbnail_url(url):
    if not isinstance(url, str) or len(url) > 8192:
        raise ValueError('光鸭未提供缩略图')
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
            or (host != 'api.guangyapan.com' and not host.endswith('.guangyacdn.com'))):
        raise ValueError('光鸭预览地址不受支持')
    return url


class GuangyapanPreviews:
    def __init__(self, data, cancel):
        data = Path(data)
        self.vault = Vault(data/'guangyapan-preview-auth'/'developer.dpapi')
        self.cache = PreviewCache(data/'cloud-previews', url_policy=thumbnail_url)
        self.cancel = cancel
        self.lock = threading.RLock()
        self.binding_lock = threading.Lock()
        self.entries = OrderedDict()
        self.bound = None
        self.credentials = {}
        try:
            self.credentials = self.vault.load()
        except (OSError, TypeError, ValueError):
            pass

    def configure(self, client_id, client_secret, account, provider):
        if not re.fullmatch(r'dvlp_[A-Za-z0-9_-]{1,80}', client_id) or not re.fullmatch(r'[A-Fa-f0-9]{64}', client_secret):
            raise ValueError('光鸭开发者凭证格式无效')
        credentials = {'client_id': client_id, 'client_secret': client_secret}
        self.match_account(credentials, provider)
        self.vault.save(credentials)
        with self.lock:
            self.credentials = credentials
            self.bound = account
            self.entries.clear()
        return True

    def post(self, endpoint, payload, credentials):
        client, secret = credentials['client_id'], credentials['client_secret']
        nonce, stamp = token_hex(12), str(int(time.time()))
        material = f'client_id={client}&client_secret={secret}&nonce={nonce}&timestamp={stamp}'
        signature = hashlib.sha512(hashlib.md5(material.encode()).digest()).hexdigest()
        headers = dict(client_id=client, nonce=nonce, timestamp=stamp, sign=signature)
        try:
            with requests.Session() as session:
                session.trust_env = False
                with session.post(DEVELOPER_URL + endpoint, json=payload, headers=headers,
                                  stream=True, allow_redirects=False, timeout=(5, 8)) as response:
                    if response.status_code != 200:
                        raise ValueError('无法访问光鸭开发者接口')
                    body = bytearray()
                    deadline = time.monotonic() + 12
                    for chunk in response.iter_content(32 * 1024):
                        body.extend(chunk)
                        if len(body) > 2 * 1024**2 or self.cancel.is_set() or time.monotonic() > deadline:
                            raise ValueError('光鸭预览请求已停止')
                    result = json.loads(body)
        except (requests.RequestException, UnicodeError, json.JSONDecodeError):
            raise ValueError('光鸭预览接口暂时不可用') from None
        if not isinstance(result, dict) or result.get('code', 0) != 0 or not isinstance(result.get('data'), dict):
            raise ValueError('光鸭开发者接口未授权或返回无效')
        return result['data']

    def root(self, credentials):
        result = self.post('get_file_list', dict(parentId='', page=0, pageSize=100, orderBy=0, sortType=0), credentials)
        if not isinstance(result.get('list'), list):
            raise ValueError('光鸭开发者接口未返回根目录')
        return {item['fileId'] for item in result['list'] if item.get('fileId')}

    def match_account(self, credentials, provider):
        expected = self.root(credentials)
        actual = {item['id'] for item in provider.list('', 0)['items']}
        # Listing order may differ. Drive file IDs are unique across accounts.
        if not expected.intersection(actual):
            raise ValueError('无法匹配开发者凭证与当前光鸭账号的根目录')

    def register(self, account, remote):
        if not account or not isinstance(remote, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', remote):
            return None
        with self.lock:
            if not self.credentials:
                return None
            key = hashlib.sha256(json.dumps(
                ['guangyapan', self.credentials['client_id'], account, remote], separators=(',', ':')
            ).encode()).hexdigest()
            self.entries[key] = account, remote
            self.entries.move_to_end(key)
            if len(self.entries) > 4096:
                self.entries.popitem(last=False)
        return key

    def load(self, key, active_account, provider):
        with self.lock:
            ref = self.entries.get(key)
            credentials = self.credentials
        if not ref or not credentials or (active_account and active_account != ref[0]):
            return None
        account, remote = ref

        def resolve():
            if active_account != account:
                raise ValueError('光鸭账号尚未连接')
            with self.binding_lock:
                if self.bound != account:
                    self.match_account(credentials, provider)
                    with self.lock:
                        if credentials is not self.credentials:
                            raise ValueError('开发者凭证已更新')
                        self.bound = account
            info = self.post('get_file_detail', {'fileId': remote}, credentials)
            file = info.get('fileInfo') or {}
            if file.get('fileId') != remote:
                raise ValueError('光鸭返回了不同文件')
            return thumbnail_url(file.get('thumbnail') or (info.get('picInfo') or {}).get('previewUrl'))

        return self.cache.load_raster(key, resolve, '', self.cancel, (480, 320))
