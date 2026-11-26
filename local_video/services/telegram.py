# SPDX-License-Identifier: GPL-2.0-only
"""Desktop Telegram commands and bounded, temporary scan previews."""
import re
from threading import Event, Lock, Thread
from urllib.parse import urlsplit
from uuid import uuid4

from ..policy import normalize_proxy
from ..security import Vault
from .telegram_archive import TelegramArchive
from .telegram_queue import TelegramQueue
from .telegram_transport import TelegramTransport, TelegramTransportError


def read_api_credentials(path):
    try:
        with open(path, 'rb') as stream:
            data = stream.read(8193)
        text = data.decode('utf-8-sig')
    except (OSError, UnicodeError):
        raise ValueError('无法读取 Telegram 应用凭证文件') from None
    ids = re.findall(r'^\s*api[ _-]*id\s*[:=：]\s*(\d{1,10})\s*$', text, re.I | re.M)
    hashes = re.findall(r'^\s*api[ _-]*hash\s*[:=：]\s*([0-9a-f]{32})\s*$', text, re.I | re.M)
    if len(data) > 8192 or len(ids) != 1 or len(hashes) != 1 or not 0 < int(ids[0]) < 2**31:
        raise ValueError('凭证文件须包含一组 api_id 和 api_hash')
    return int(ids[0]), hashes[0]


class TelegramService:
    def __init__(self, data, store, libraries, collection, groups, native, settings, notify):
        self._native, self._libraries, self._settings = native, libraries, settings
        self._proxy_vault = Vault(data/'telegram-proxy.dpapi')
        self._proxy_config = None
        self._lock = Lock()
        self._account_lock = Lock()
        self._worker = None
        self._scan = None
        self._closed = False
        self._transport = TelegramTransport(Vault(data/'telegram-session.dpapi'), self._effective_proxy)
        self._archive = TelegramArchive(store, libraries, collection, groups, native)
        self._queue = TelegramQueue(store, libraries, self._transport, self._archive.register, notify)

    def _proxy_settings(self):
        if self._proxy_config is None:
            try:
                config = self._proxy_vault.load()
                if not isinstance(config.get('url', ''), str):
                    raise ValueError()
                self._proxy_config = config
            except Exception:
                raise ValueError('无法读取 Telegram 代理配置，请重新设置') from None
        return self._proxy_config

    def _effective_proxy(self):
        config = self._proxy_settings()
        if config.get('direct') is True:
            return ''
        return config.get('url') or self._settings.credentials.get('proxy', '')

    def _proxy_status(self):
        config = self._proxy_settings()
        if config.get('direct') is True:
            return 'direct', None
        url = config.get('url', '')
        if not url:
            return 'inherit', None
        parts = urlsplit(url)
        mode = {'socks5': 'socks5', 'socks5h': 'socks5', 'http': 'http'}.get(parts.scheme)
        if mode and parts.hostname in ('127.0.0.1', 'localhost') and parts.port and parts.username is None:
            return mode, parts.port
        return 'existing', None

    def status(self, p):
        status = self._transport.status()
        try:
            mode, port = self._proxy_status()
            status.update(has_proxy=bool(self._effective_proxy()), proxy_mode=mode, proxy_port=port)
        except ValueError:
            status.update(has_proxy=True, proxy_mode='existing', proxy_port=None)
        return status

    def _ensure_idle(self):
        if (self._worker and self._worker.is_alive()) or self._queue.busy():
            raise ValueError('请先暂停 Telegram 下载或等待扫描结束')

    def proxy(self, p):
        if set(p) == {'url'}:
            try:
                url = normalize_proxy(p['url'])
                parts = urlsplit(url)
                if url and (parts.scheme not in ('http', 'socks5h') or parts.path not in ('', '/')
                            or parts.query or parts.fragment):
                    raise ValueError()
            except ValueError:
                raise ValueError('请输入 SOCKS5 或 HTTP CONNECT 代理地址') from None
            config = {'url': url}
        else:
            mode = p.get('mode')
            if mode in ('inherit', 'direct') and set(p) == {'mode'}:
                config = {'url': '', 'direct': mode == 'direct'}
            elif mode in ('socks5', 'http') and set(p) == {'mode', 'port'}:
                port = p['port']
                if not 1 <= port <= 65535:
                    raise ValueError('代理端口须为 1 到 65535')
                scheme = 'socks5h' if mode == 'socks5' else 'http'
                config = {'url': f'{scheme}://127.0.0.1:{port}'}
            else:
                raise ValueError('请选择连接方式并填写所需端口')
        with self._account_lock:
            self._ensure_idle()
            try:
                previous = self._effective_proxy()
            except ValueError:
                previous = None
            self._proxy_vault.save(config)
            self._proxy_config = config
            if previous != self._effective_proxy() and self._transport.status().get('code_sent'):
                self._transport.reset_code()
        return self.status({})

    def _choose_credentials(self):
        return self._native.choose_telegram_credentials()

    def import_credentials(self, p):
        with self._account_lock:
            self._ensure_idle()
            path = self._choose_credentials()
            if path:
                self._save_credentials(*read_api_credentials(path))
        return self.status({})

    def manual_credentials(self, p):
        with self._account_lock:
            self._ensure_idle()
            self._save_credentials(p['api_id'].strip(), p['api_hash'])
        return self.status({})

    def _save_credentials(self, api_id, api_hash):
        self._transport.configure(api_id, api_hash)
        self._transport.reset_code()
        with self._lock:
            self._scan = None

    def send_code(self, p):
        with self._account_lock:
            return self._transport.send_code(p['phone'])

    def resend_code(self, p):
        with self._account_lock:
            return self._transport.resend_code()

    def reset_code(self, p):
        with self._account_lock:
            return self._transport.reset_code()

    def sign_in(self, p):
        with self._account_lock:
            return self._transport.sign_in(code=p.get('code', ''), password=p.get('password', ''))

    def logout(self, p):
        with self._account_lock:
            self._ensure_idle()
            result = self._transport.logout()
            with self._lock:
                self._scan = None
            return result

    def channels(self, p):
        return self._transport.channels()

    def scan(self, p):
        if (p['mode'] not in ('link', 'channel') or not 1 <= p['limit'] <= 500
                or p['media_mode'] not in ('video', 'image', 'all') or not p['source'].strip()):
            raise ValueError('Telegram 扫描参数无效')
        with self._account_lock, self._lock:
            if self._closed:
                raise ValueError('窗口正在关闭')
            if self._worker and self._worker.is_alive():
                raise ValueError('上一次 Telegram 扫描仍在结束，请稍候')
            scan_id = uuid4().hex
            self._scan = dict(id=scan_id, state='scanning', items={}, error='', cancelled=Event())
            self._worker = Thread(target=self._read, args=(self._scan, p),
                                  name='x2lt-telegram-scan', daemon=True)
            self._worker.start()
        return {'id': scan_id}

    def _read(self, scan, p):
        try:
            items = self._transport.scan(p['mode'], p['source'].strip(), p['limit'], p['media_mode'],
                                         cancelled=scan['cancelled'].is_set)
            items = {uuid4().hex: item for item in items}
            state, error = 'ready', ''
        except TelegramTransportError as exc:
            items, state, error = {}, 'failed', str(exc)
        except Exception:
            items, state, error = {}, 'failed', '无法读取 Telegram 消息'
        with self._lock:
            if scan['state'] == 'scanning':
                scan.update(state=state, items=items, error=error)
            self._worker = None

    def _get_scan(self, identifier):
        if not self._scan or identifier != self._scan['id']:
            raise ValueError('扫描结果已失效，请重新扫描')
        return self._scan

    def scan_status(self, p):
        with self._lock:
            scan = self._get_scan(p['id'])
            items = [dict(id=identifier, **{key: item[key] for key in ('title', 'kind', 'size', 'peer_title')})
                     for identifier, item in scan['items'].items()]
            return dict(state=scan['state'], error=scan['error'], items=items)

    def cancel_scan(self, p):
        with self._lock:
            scan = self._get_scan(p['id'])
            if scan['state'] == 'scanning':
                scan['state'] = 'cancelled'
                scan['cancelled'].set()
            return True

    def enqueue(self, p):
        with self._account_lock, self._lock:
            scan = self._get_scan(p['scan_id'])
            ids = list(dict.fromkeys(p['ids']))
            if scan['state'] != 'ready' or not ids or not all(identifier in scan['items'] for identifier in ids):
                raise ValueError('请选择扫描结果中的媒体')
            if not self._transport.status().get('authorized'):
                raise ValueError('请先登录 Telegram')
            items = [scan['items'][identifier] for identifier in ids]
            return self._queue.add(items, p['library_id'])

    def jobs(self, p):
        return self._queue.list()

    def control(self, p):
        with self._account_lock:
            if p['action'] in ('resume', 'retry') and not self._transport.status().get('authorized'):
                raise ValueError('请先登录 Telegram')
            return self._queue.control(p['action'], p['ids'])

    def open_source(self, p):
        return self._archive.open_source(p['library_id'], p['id'])

    def close(self):
        self._closed = True
        with self._lock:
            if self._scan:
                self._scan['cancelled'].set()
        stopped = self._queue.close()
        self._transport.close()
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(1)
        return stopped or self._queue.close()
