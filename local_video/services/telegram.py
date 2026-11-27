# SPDX-License-Identifier: GPL-2.0-only
"""Desktop Telegram commands and bounded, temporary scan previews."""
import re
from base64 import b64encode
from datetime import date, datetime, time, timedelta
from hashlib import sha256
from io import BytesIO
from threading import Event, Lock, Thread
from urllib.parse import urlsplit
from uuid import uuid4

from ..policy import normalize_proxy
from ..images import read_image
from ..security import Vault
from ..transfer import Paused
from .telegram_archive import TelegramArchive
from .telegram_queue import TASK_CARD_FIELDS, TelegramQueue, task_card_keys
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
    def __init__(self, data, store, libraries, collection, groups, native, settings, notify, previews, local_preview):
        self._native, self._libraries, self._settings = native, libraries, settings
        self._proxy_vault = Vault(data/'telegram-proxy.dpapi')
        self._proxy_config = None
        self._lock = Lock()
        self._account_lock = Lock()
        self._worker = None
        self._scan = None
        self._closed = False
        self._previews = previews
        self._local_preview = local_preview
        self._preview_cancel = Event()
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
            if previous != self._effective_proxy():
                if self._transport.status().get('code_sent'):
                    self._transport.reset_code()
                if self._transport.qr_status()['state'] in ('waiting', 'password'):
                    self._transport.qr_cancel()
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

    def qr_start(self, p):
        try:
            import qrcode
            from qrcode.image.pil import PilImage
        except ImportError:
            raise ValueError('缺少二维码组件，请更新桌面运行依赖') from None

        with self._account_lock:
            self._ensure_idle()
            state = dict(self._transport.qr_start())
            url = state.pop('url', '')
            if state['state'] != 'waiting':
                return state
            if not re.fullmatch(r'tg://login\?token=[A-Za-z0-9_-]{1,2048}', url):
                self._transport.qr_cancel()
                raise ValueError('Telegram 未返回有效的登录二维码')
            try:
                with BytesIO() as buffer:
                    image = qrcode.make(url, box_size=5, image_factory=PilImage)
                    image.save(buffer, format='PNG')
                    state['image'] = 'data:image/png;base64,' + b64encode(buffer.getvalue()).decode('ascii')
            except Exception:
                self._transport.qr_cancel()
                raise ValueError('无法生成 Telegram 登录二维码') from None
            return state

    def qr_status(self, p):
        return self._transport.qr_status()

    def qr_cancel(self, p):
        with self._account_lock:
            return self._transport.qr_cancel()

    def logout(self, p):
        with self._account_lock:
            self._ensure_idle()
            result = self._transport.logout()
            with self._lock:
                self._scan = None
            return result

    def channels(self, p):
        return self._transport.channels()

    @staticmethod
    def _scan_dates(p):
        if 'start_date' not in p and 'end_date' not in p:
            return None, None
        try:
            if p['mode'] != 'channel' or any(not re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.get(key, ''))
                                              for key in ('start_date', 'end_date')):
                raise ValueError()
            start, end = date.fromisoformat(p['start_date']), date.fromisoformat(p['end_date'])
            if start > end:
                raise ValueError()
            start_at = int(datetime.combine(start, time.min).timestamp())
            end_at = int(datetime.combine(end + timedelta(days=1), time.min).timestamp())
            return start_at, end_at
        except (ValueError, OverflowError, OSError):
            raise ValueError('请选择有效的起止日期') from None

    def scan(self, p):
        if (p['mode'] not in ('link', 'channel')
                or p['media_mode'] not in ('video', 'image', 'all') or not p['source'].strip()):
            raise ValueError('Telegram 扫描参数无效')
        dates = self._scan_dates(p)
        if dates[0] is None and not 1 <= p['limit'] <= 500:
            raise ValueError('最近消息数须为 1 到 500')
        with self._account_lock, self._lock:
            if self._closed:
                raise ValueError('窗口正在关闭')
            if self._worker and self._worker.is_alive():
                raise ValueError('上一次 Telegram 扫描仍在结束，请稍候')
            scan_id = uuid4().hex
            self._scan = dict(id=scan_id, state='scanning', items={}, keys=set(), error='', cancelled=Event())
            self._worker = Thread(target=self._read, args=(self._scan, p, dates),
                                  name='x2lt-telegram-scan', daemon=True)
            self._worker.start()
        return {'id': scan_id}

    def _read(self, scan, p, dates):
        def publish(item):
            identity = (item['peer_id'], item['message_id'], item['media_id'])
            with self._lock:
                if scan['state'] != 'scanning' or identity in scan['keys']:
                    return
                scan['keys'].add(identity)
                if forward := item.get('forward_id'):
                    item = {**item, 'avatar_key': sha256(f'telegram:avatar:{forward}'.encode()).hexdigest()}
                scan['items'][uuid4().hex] = item

        try:
            limit = p['limit'] if dates[0] is None else 50
            items = self._transport.scan(p['mode'], p['source'].strip(), limit, p['media_mode'],
                                         cancelled=scan['cancelled'].is_set, start_at=dates[0], end_at=dates[1],
                                         on_item=publish)
            for item in items:
                publish(item)
            state, error = 'ready', ''
        except TelegramTransportError as exc:
            state, error = 'failed', str(exc)
        except Exception:
            state, error = 'failed', '无法读取 Telegram 消息'
        with self._lock:
            if scan['state'] == 'scanning':
                scan.update(state=state, error=error)
            self._worker = None

    def _get_scan(self, identifier):
        if not self._scan or identifier != self._scan['id']:
            raise ValueError('扫描结果已失效，请重新扫描')
        return self._scan

    def scan_status(self, p):
        with self._lock:
            scan = self._get_scan(p['id'])
            rows = list(scan['items'].items())
            state, error = scan['state'], scan['error']
        target = self._libraries.get(p['library_id'])['id'] if p.get('library_id') else None
        peers = {str(item['peer_id']) for _, item in rows}
        states = {peer: self._queue.states(target, peer) for peer in peers} if target else {}
        saved = {peer: self._archive.saved_media(target, peer) for peer in peers} if target else {}
        fields = ('title', 'kind', 'size', 'peer_title', *TASK_CARD_FIELDS)
        items = [dict(id=identifier, **task_card_keys(item),
                      download_state=states.get(str(item['peer_id']), {}).get(
                          (str(item['message_id']), str(item['media_id'])),
                          'completed' if f"{item['message_id']}:{item['media_id']}" in
                          saved.get(str(item['peer_id']), ()) else 'new'),
                      **{key: item[key] for key in fields if key in item})
                 for identifier, item in rows]
        return dict(state=state, error=error, items=items)

    def _preview_data(self, item, kind, *, cached_only=False):
        if item is None:
            return None
        size = (128, 128) if kind == 'avatar' else (480, 480)
        key = item.get('avatar_key') if kind == 'avatar' else sha256(
            f"telegram:thumbnail:{task_card_keys(item)['media_key']}".encode()).hexdigest()
        if not key:
            return None
        def generate():
            raw = None if cached_only else self._transport.preview(item, kind)
            return read_image(BytesIO(raw), size=size) if raw else None
        try:
            raster = self._previews.load_generated(key, generate, self._preview_cancel, size)
            if raster is None:
                return None
            with raster, BytesIO() as output:
                raster.save(output, format='JPEG', quality=85)
                return 'data:image/jpeg;base64,' + b64encode(output.getvalue()).decode('ascii')
        except (TelegramTransportError, ValueError, OSError, Paused):
            return None

    def scan_preview(self, p):
        kind = p.get('kind', 'media')
        if kind not in ('media', 'avatar'):
            raise ValueError('Telegram 预览类型无效')
        with self._lock:
            scan = self._get_scan(p['scan_id'])
            item = scan['items'].get(p['id'])
        return self._preview_data(item, kind)

    def forward_avatar(self, p):
        key = p['key']
        if not re.fullmatch(r'[0-9a-f]{64}', key):
            raise ValueError('Telegram 头像编号无效')
        if item := self._queue.avatar_item(key):
            return {'url': self._preview_data(item, 'avatar')}
        return {'url': self._preview_data(self._archive.saved_avatar(key), 'avatar', cached_only=True)}

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
            if scan['state'] not in ('ready', 'cancelled', 'scanning', 'failed') or not ids or not all(
                    identifier in scan['items'] for identifier in ids):
                raise ValueError('请选择扫描结果中的媒体')
            if not self._transport.status().get('authorized'):
                raise ValueError('请先登录 Telegram')
            items = [scan['items'][identifier] for identifier in ids]
            library = self._libraries.get(p['library_id'])['id']
            saved = {peer: self._archive.saved_media(library, peer)
                     for peer in {str(item['peer_id']) for item in items}}
            return self._queue.add([item for item in items if f"{item['message_id']}:{item['media_id']}"
                                    not in saved[str(item['peer_id'])]], library)

    def jobs(self, p):
        return self._queue.list(visible_only=True)

    def job_preview(self, p):
        found = self._queue.preview_job(p['id'])
        if found is None:
            return None
        job, item = found
        if job['state'] == 'completed':
            if cached := self._preview_data(item, 'media', cached_only=True):
                return cached
            if location := self._archive.preview_location(job):
                if preview := self._local_preview(location):
                    return preview
        return self._preview_data(item, 'media')

    def control(self, p):
        with self._account_lock:
            if p['action'] in ('resume', 'retry') and not self._transport.status().get('authorized'):
                raise ValueError('请先登录 Telegram')
            return self._queue.control(p['action'], p['ids'], delete_partial=False,
                                       report=p.get('report', False))

    def bulk_control(self, p):
        action = p['action']
        if action not in ('resume', 'cancel'):
            raise ValueError('Telegram 批量下载操作无效')
        with self._account_lock:
            if action == 'resume' and not self._transport.status().get('authorized'):
                raise ValueError('请先登录 Telegram')
            return self._queue.control(action, delete_partial=p.get('delete_partial', False),
                                       report=p.get('report', False))

    def open_source(self, p):
        return self._archive.open_source(p['library_id'], p['id'], p.get('content_id'))

    def close(self):
        self._closed = True
        self._preview_cancel.set()
        with self._lock:
            if self._scan:
                self._scan['cancelled'].set()
        stopped = self._queue.close()
        self._transport.close()
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(1)
        return stopped or self._queue.close()
