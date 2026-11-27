from types import SimpleNamespace
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timedelta, timezone
import threading
import unittest
from unittest.mock import patch

from local_video.services.telegram_transport import TelegramTransport, TelegramTransportError


class MemoryVault:
    def __init__(self):
        self.value = {}

    def load(self):
        return self.value.copy()

    def save(self, value):
        self.value = value.copy()


class Channel(SimpleNamespace):
    pass


class PeerChannel(SimpleNamespace):
    @property
    def id(self):
        return self.channel_id


class MessageMediaDocument(SimpleNamespace):
    pass


class Document(SimpleNamespace):
    pass


class MessageMediaPhoto(SimpleNamespace):
    pass


class Photo(SimpleNamespace):
    pass


class PhotoSize(SimpleNamespace):
    pass


class PhotoSizeProgressive(SimpleNamespace):
    pass


class PhotoCachedSize(SimpleNamespace):
    pass


class InputPhotoFileLocation(SimpleNamespace):
    pass


class SessionPasswordNeededError(Exception):
    pass


class PasswordHashInvalidError(Exception):
    pass


class FloodWaitError(Exception):
    seconds = 21


class FileReferenceExpiredError(Exception):
    pass


class Session:
    def __init__(self, value=''):
        self.value = value

    def save(self):
        return 'synthetic-session'


class QRLogin:
    url = 'tg://login?token=synthetic-private-token'

    def __init__(self, client):
        self.client = client
        self.expires = datetime.now(timezone.utc) + timedelta(seconds=30)
        self.outcome = client.loop.create_future()
        self.listening = False

    async def wait(self):
        self.listening = True
        try:
            result = await self.outcome
            if isinstance(result, Exception):
                raise result
            self.client.authorized = True
        finally:
            self.listening = False


class Client:
    def __init__(self, session, *args, **kwargs):
        self.session = session
        self.options = kwargs
        self.loop = asyncio.get_running_loop()
        self.connected = False
        self.authorized = bool(session.value)
        self.peer = Channel(id=989, username='example', title='测试频道')
        self.origin = Channel(id=660, username='source', title='来源频道')
        self.account = SimpleNamespace(id=721)
        self.messages = {3: make_message(3), 4: make_message(4, group=700), 5: make_message(5, group=700),
                         6: make_message(6, noforwards=True)}
        self.saved_messages = {}
        self.offsets = []
        self.files = []
        self.requested = []
        self.history_requests = []
        self.history_ids = []
        self.history_failure_at = None
        self.previews = []
        self.download_failures = []
        self.logouts = 0
        self.downloads_closed = 0

    async def connect(self):
        assert self.loop is asyncio.get_running_loop()
        self.connected = True

    def is_connected(self):
        return self.connected

    async def disconnect(self):
        self.connected = False

    async def get_me(self):
        return self.account if self.authorized else None

    async def send_code_request(self, phone):
        return SimpleNamespace(phone_code_hash='synthetic-code-hash',
                               type=type('SentCodeTypeApp', (), {})(), next_type=None, timeout=None)

    async def qr_login(self):
        self.qr = QRLogin(self)
        return self.qr

    async def sign_in(self, **kwargs):
        if kwargs.get('code') == '12345':
            raise SessionPasswordNeededError('secret')
        self.authorized = True

    async def log_out(self):
        self.logouts += 1
        self.connected = self.authorized = False
        return True

    async def iter_dialogs(self):
        yield SimpleNamespace(entity=self.peer)

    async def get_entity(self, source):
        assert source in ('example', -1000000000989, -1000000000660), source
        if source == -1000000000660:
            return self.origin
        return self.peer

    async def get_messages(self, peer, *, ids):
        self.requested.append(ids)
        messages = self.saved_messages if peer is self.account else self.messages
        if isinstance(ids, int):
            return messages.get(ids)
        return [messages.get(message_id) for message_id in ids]

    async def iter_messages(self, peer, *, limit, offset_date=None, min_id=0, max_id=0, offset_id=0):
        self.history_requests.append((limit, offset_date))
        offset_id = max(offset_id, max_id)
        messages = self.saved_messages if peer is self.account else self.messages
        for message in [row for row in sorted(messages.values(), key=lambda row: row.id, reverse=True)
                        if row.id > min_id and (not offset_id or row.id < offset_id)
                        and (offset_date is None or row.date < offset_date)][:limit]:
            if message.id == self.history_failure_at:
                raise FloodWaitError()
            self.history_ids.append(message.id)
            yield message

    async def download_media(self, message, *, file, thumb):
        self.previews.append((message.id, file, thumb))
        return b'thumbnail'

    async def download_profile_photo(self, entity, *, file, download_big):
        self.previews.append((entity.id, file, download_big))
        return b'avatar'

    async def iter_download(self, file, *, offset, file_size, dc_id):
        self.offsets.append(offset)
        self.files.append((file, file_size, dc_id))
        try:
            if self.download_failures:
                failure_after_one = self.download_failures.pop(0)
                yield b'abc'[offset:]
                raise failure_after_one
            yield b'abcdef'[offset:]
        finally:
            self.downloads_closed += 1


def make_message(message_id, *, group=None, noforwards=False):
    document = Document(id=200 + message_id, dc_id=3, size=6, mime_type='video/mp4')
    return SimpleNamespace(id=message_id, chat_id=-1000000000989, message='示例\n正文', grouped_id=group,
                           date=datetime.fromtimestamp(1700000000 + message_id, tz=timezone.utc),
                           noforwards=noforwards, media=MessageMediaDocument(document=document),
                           file=SimpleNamespace(name='raw.mp4', ext='.mp4', width=1280, height=720))


class TelegramTransportTests(unittest.TestCase):
    def setUp(self):
        self.vault = MemoryVault()
        self.proxy = 'socks5h://synthetic:demo@localhost:1080'
        self.clients = []

        def make_client(*args, **kwargs):
            client = Client(*args, **kwargs)
            self.clients.append(client)
            return client

        types = SimpleNamespace(**{kind.__name__: kind for kind in
                                  (Channel, PeerChannel, Document, MessageMediaDocument, MessageMediaPhoto, Photo,
                                   PhotoSize, PhotoSizeProgressive, PhotoCachedSize, InputPhotoFileLocation)})
        utils = SimpleNamespace(get_peer_id=lambda entity: -(10**12 + entity.id))
        errors = SimpleNamespace(**{kind.__name__: kind for kind in
                                   (SessionPasswordNeededError, PasswordHashInvalidError,
                                    FloodWaitError, FileReferenceExpiredError)})
        patcher = patch('local_video.services.telegram_transport._load_telethon',
                        return_value=SimpleNamespace(client=make_client, session=Session, types=types,
                                                     utils=utils, errors=errors))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.transport = TelegramTransport(self.vault, lambda: self.proxy)
        self.addCleanup(self.transport.close)

    def login(self):
        self.transport.configure(6789, '0' * 32)
        self.transport.send_code('+12025550123')
        self.transport.sign_in('54321')
        return self.clients[-1]

    def resolve_qr(self, client, result=None):
        # This callback enqueues the waiter before the next transport request.
        client.loop.call_soon_threadsafe(client.qr.outcome.set_result, result)

    def test_qr_listens_before_returning_and_saves_only_the_session(self):
        self.transport.configure(6789, '0' * 32)
        self.assertEqual(self.transport.qr_status(), {'state': 'idle'})
        state = self.transport.qr_start()
        client = self.clients[-1]
        self.assertTrue(client.options['receive_updates'])
        self.assertTrue(client.qr.listening)
        self.assertEqual(state['url'], QRLogin.url)
        self.assertEqual(self.transport.qr_status(),
                         {'state': 'waiting', 'expires_at': int(client.qr.expires.timestamp() * 1000)})
        self.assertFalse(self.vault.value['session'])
        self.resolve_qr(client)
        self.assertEqual(self.transport.qr_status(), {'state': 'authorized'})
        self.assertEqual(self.vault.value, {'api_id': 6789, 'api_hash': '0' * 32, 'session': 'synthetic-session'})
        self.transport.channels()
        self.assertFalse(self.clients[-1].options['receive_updates'])
        self.assertFalse(client.connected)

    def test_qr_password_retry_without_phone_code(self):
        self.transport.configure(6789, '0' * 32)
        self.transport.qr_start()
        client = self.clients[-1]
        self.resolve_qr(client, SessionPasswordNeededError('synthetic-private-token'))
        self.assertEqual(self.transport.qr_status(), {'state': 'password'})
        self.assertTrue(self.transport.status()['password_required'])
        self.assertFalse(self.transport.status()['code_sent'])
        with patch.object(client, 'sign_in', side_effect=PasswordHashInvalidError('fixture-secret')):
            with self.assertRaises(TelegramTransportError) as failure:
                self.transport.sign_in(password='fixture-password')
        self.assertEqual(failure.exception.code, 'password')
        self.assertNotIn('fixture', str(failure.exception))
        self.assertEqual(self.transport.qr_status(), {'state': 'password'})
        self.assertTrue(self.transport.sign_in(password='fixture-password')['authorized'])
        self.assertFalse(self.transport.status()['password_required'])
        self.assertFalse(client.connected)

    def test_qr_after_failed_phone_attempt_replaces_unauthorized_client(self):
        self.transport.configure(6789, '0' * 32)
        with patch.object(Client, 'send_code_request', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(TelegramTransportError):
                self.transport.send_code('+12025550123')
        phone_client = self.clients[-1]
        self.transport.qr_start()
        self.resolve_qr(self.clients[-1])
        self.assertEqual(self.transport.qr_status(), {'state': 'authorized'})
        self.assertEqual(self.transport.channels(), [{'id': '-1000000000989', 'title': '测试频道'}])
        self.assertFalse(phone_client.connected)

    def test_qr_cancel_restart_and_phone_switch_disconnect_previous_listener(self):
        self.transport.configure(6789, '0' * 32)
        self.transport.qr_start()
        first = self.clients[-1]
        self.assertEqual(self.transport.qr_cancel(), {'state': 'idle'})
        self.assertFalse(first.connected)
        self.assertFalse(first.qr.listening)
        self.transport.qr_start()
        second = self.clients[-1]
        self.assertIsNot(first, second)
        self.assertTrue(self.transport.send_code('+12025550123')['code_sent'])
        self.assertFalse(second.connected)
        self.assertFalse(second.qr.listening)
        self.assertFalse(self.vault.value['session'])
        self.assertFalse(self.clients[-1].options['receive_updates'])

    def test_qr_expiry_and_failure_are_reported_without_token(self):
        self.transport.configure(6789, '0' * 32)
        self.transport.qr_start()
        self.resolve_qr(self.clients[-1], TimeoutError())
        self.assertEqual(self.transport.qr_status(), {'state': 'expired'})
        self.transport.qr_start()
        self.resolve_qr(self.clients[-1], RuntimeError(QRLogin.url))
        result = self.transport.qr_status()
        self.assertEqual(result['state'], 'failed')
        self.assertNotIn('synthetic-private-token', str(result))

    def test_qr_cannot_save_a_session_after_proxy_or_credentials_change(self):
        self.transport.configure(6789, '0' * 32)
        self.transport.qr_start()
        client = self.clients[-1]
        self.proxy = 'http://localhost:8080'
        self.resolve_qr(client)
        self.assertEqual(self.transport.qr_status(), {'state': 'idle'})
        self.assertFalse(self.vault.value['session'])
        self.transport.qr_start()
        client = self.clients[-1]
        self.transport.configure(6789, '1' * 32)
        self.assertFalse(client.qr.listening)
        self.assertFalse(client.connected)
        self.assertEqual(self.transport.qr_status(), {'state': 'idle'})

    def test_close_cancels_qr_listener_and_disconnects(self):
        self.transport.configure(6789, '0' * 32)
        self.transport.qr_start()
        client = self.clients[-1]
        self.transport.close()
        self.assertFalse(client.connected)
        self.assertFalse(client.qr.listening)

    def test_code_delivery_and_resend_respect_server_timeout(self):
        from telethon.tl import functions, types

        self.transport.configure(6789, '0' * 32)
        response = types.auth.SentCode(types.auth.SentCodeTypeApp(5), 'fixture-private',
                                       next_type=types.auth.CodeTypeSms(), timeout=45)
        with patch.object(Client, 'send_code_request', return_value=response) as send:
            state = self.transport.send_code('+12025550123')
            self.assertEqual(state['code_delivery'], 'app')
            self.assertTrue(state['code_can_resend'])
            self.assertGreater(state['code_resend_at'], 0)
            self.assertNotIn('fixture-private', str(state))
            with self.assertRaisesRegex(TelegramTransportError, '等待'):
                self.transport.resend_code()
            self.transport.send_code('+12025550123')
            self.assertEqual(send.await_count, 1)
        self.transport._sdk.functions = functions
        received = []

        async def resend(client, request):
            received.append(request)
            return types.auth.SentCode(types.auth.SentCodeTypeSms(5), 'another-private')

        with (patch.object(Client, '__call__', resend, create=True),
              patch('local_video.services.telegram_transport.time', return_value=state['code_resend_at']/1000+1)):
            state = self.transport.resend_code()
        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], functions.auth.ResendCodeRequest)
        self.assertEqual(received[0].phone_number, '12025550123')
        self.assertEqual(state['code_delivery'], 'sms')
        self.assertFalse(state['code_can_resend'])
        self.transport.reset_code()
        self.assertFalse(self.transport.status()['code_sent'])
        self.assertFalse(self.clients[-1].connected)

    def test_official_client_requirement_is_not_reported_as_code_sent(self):
        from telethon.tl import types

        self.transport.configure(6789, '0' * 32)
        response = types.auth.SentCodePaymentRequired('fixture', 'private-hash', '', '', 1, 'USD', 100)
        with patch.object(Client, 'send_code_request', return_value=response):
            with self.assertRaisesRegex(TelegramTransportError, '官方客户端'):
                self.transport.send_code('+12025550123')
        self.assertFalse(self.transport.status()['code_sent'])

    def test_login_persists_session_and_reuses_loop_including_after_proxy_change(self):
        self.assertFalse(self.transport.status()['configured'])
        self.assertFalse(self.transport.configure('6789', '0' * 32)['authorized'])
        self.assertEqual(self.clients, [])
        self.assertTrue(self.transport.send_code('+12025550123')['code_sent'])
        self.assertEqual(self.clients[0].options['proxy']['proxy_type'], 'socks5')
        self.assertEqual(self.clients[0].options['proxy']['username'], 'synthetic')
        self.assertFalse(self.vault.value.get('session'))
        self.assertTrue(self.transport.sign_in('12345')['password_required'])
        self.assertTrue(self.transport.sign_in(password='demo')['authorized'])
        self.assertEqual(self.vault.value['session'], 'synthetic-session')
        self.assertNotIn('password', self.vault.value)
        self.assertNotIn('phone', self.vault.value)
        self.assertTrue(self.transport.status()['authorized'])

        self.proxy = 'http://localhost:8080'
        rows = []
        thread = threading.Thread(target=lambda: rows.extend(self.transport.channels()))
        thread.start()
        thread.join(5)
        self.assertEqual(rows, [{'id': '-1000000000989', 'title': '测试频道'}])
        self.assertIs(self.clients[0].loop, self.clients[1].loop)
        self.assertFalse(self.clients[0].connected)
        self.assertEqual(self.clients[1].options['proxy']['proxy_type'], 'http')
        self.assertFalse(self.transport.logout()['authorized'])
        self.assertEqual(self.clients[1].logouts, 1)
        self.assertFalse(self.vault.value.get('session'))

    def test_scan_exact_album_member_private_link_and_skip_protected_or_external_media(self):
        client = self.login()
        album = self.transport.scan('link', 'https://t.me/c/989/40/4?single', 15, 'all')
        self.assertEqual([row['message_id'] for row in album], ['4'])
        self.assertEqual(client.requested, [4])
        self.assertEqual(album[0]['media_id'], '204')
        self.assertEqual(album[0]['group_id'], '700')
        self.assertEqual(album[0]['size'], 6)
        self.assertEqual(album[0]['body'], '示例\n正文')
        self.assertEqual(album[0]['url'], 'https://t.me/example/4')
        scanned = self.transport.scan('channel', '-1000000000989', 15, 'video')
        self.assertEqual([row['message_id'] for row in scanned], ['5', '4', '3'])
        self.assertEqual(self.transport.scan('channel', '-1000000000989', 15, 'image'), [])
        with self.assertRaises(TelegramTransportError):
            self.transport.scan('link', 'https://t.me.evil.test/example/4', 15, 'all')
        with self.assertRaisesRegex(TelegramTransportError, '内容保护'):
            self.transport.scan('link', 'https://t.me/example/6', 15, 'all')
        client.peer.noforwards = True
        with self.assertRaisesRegex(TelegramTransportError, '内容保护'):
            self.transport.scan('channel', '-1000000000989', 15, 'all')

    def test_title_uses_filename_or_media_kind_when_caption_is_blank(self):
        client = self.login()
        client.messages[3].message = '  '
        item, = self.transport.scan('link', 't.me/example/3', 1, 'all')
        self.assertEqual(item['title'], 'raw.mp4')
        client.messages[3].file.name = ''
        item, = self.transport.scan('link', 't.me/example/3', 1, 'all')
        self.assertEqual(item['title'], '视频')

    def test_scan_date_range_uses_exclusive_end_and_publishes_progress_without_count_cap(self):
        client = self.login()
        client.messages = {index: make_message(index) for index in range(1, 806)}
        seen = []
        rows = self.transport.scan('channel', '-1000000000989', 1, 'all',
                                   start_at=1700000003, end_at=1700000804.5, on_item=seen.append)
        self.assertEqual(client.history_requests, [(None, datetime.fromtimestamp(1700000805, timezone.utc))])
        self.assertEqual(rows, seen)
        self.assertEqual(len(rows), 802)
        self.assertEqual(rows[0]['sent_at'], 1700000804)
        self.assertEqual(rows[-1]['sent_at'], 1700000003)
        self.assertEqual(client.history_ids[-1], 2)
        self.assertEqual(rows[0]['forward_id'], '')
        self.assertEqual(rows[0]['forward_title'], '')
        self.assertEqual(rows[0]['forward_url'], '')

    def test_cancel_scan_after_publishing_first_item(self):
        self.login()
        seen = []
        self.assertEqual(self.transport.scan('channel', '-1000000000989', 15, 'all',
                                             cancelled=lambda: bool(seen), on_item=seen.append), [])
        self.assertEqual([row['message_id'] for row in seen], ['5'])

    def test_saved_messages_are_bound_to_the_logged_in_account(self):
        client = self.login()
        message = make_message(3)
        message.chat_id = client.account.id
        message.media.document.thumbs = [PhotoSize(w=120, h=80, type='m', size=5)]
        protected = make_message(4, noforwards=True)
        protected.chat_id = client.account.id
        client.saved_messages = {3: message, 4: protected, 5: make_message(5)}
        item, = self.transport.scan('saved', '', 50, 'all')
        self.assertEqual((item['peer_id'], item['message_id'], item['url']), ('saved:721', '3', ''))
        self.assertEqual(self.transport.preview(item), b'thumbnail')
        self.assertEqual(b''.join(self.transport.chunks(item, 3)), b'def')
        client.account = SimpleNamespace(id=722)
        with self.assertRaisesRegex(TelegramTransportError, '账号'):
            self.transport.preview(item)
        with self.assertRaisesRegex(TelegramTransportError, '账号'):
            list(self.transport.chunks(item, 0))

    def test_incremental_scan_keeps_fixed_window_and_discovery_across_restart(self):
        from local_video.services.telegram_discovery import TelegramDiscovery
        from local_video.store import Store

        client = self.login()
        client.messages = {index: make_message(index) for index in range(1, 101)}
        client.messages[90].media = None
        client.messages[89].noforwards = True
        photo = Photo(id=777, access_hash=11, file_reference=b'ref', dc_id=2,
                      sizes=[PhotoSize(w=120, h=80, type='x', size=8)])
        client.messages[88].media = MessageMediaPhoto(photo=photo)
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'db.sqlite'
            store = Store(path)
            journal = TelegramDiscovery(store)
            scan = lambda **options: self.transport.scan('channel', '-1000000000989', 50, 'video', **options)
            observed = []
            try:
                scan(incremental=journal, start_at=1700000001, end_at=1700000091,
                     on_item=observed.append, cancelled=lambda: len(observed) == 3)
                self.assertEqual([int(item['message_id']) for item in observed], [88, 87, 86])
            finally:
                store.db.close()
            client.messages.update({index: make_message(index) for index in range(101, 159)})
            store = Store(path)
            try:
                journal = TelegramDiscovery(store)
                resumed = scan(incremental=journal)
                self.assertEqual([int(item['message_id']) for item in resumed], list(range(88, 0, -1)))
                self.assertEqual(resumed[0]['kind'], 'image')
                client.history_failure_at = 99
                with self.assertRaises(TelegramTransportError) as failure:
                    scan(incremental=journal)
                self.assertEqual(failure.exception.code, 'flood_wait')
                client.history_failure_at = None
                client.history_ids.clear()
                new = scan(incremental=journal)
                self.assertEqual(client.history_ids[0], 99)
                self.assertEqual([int(item['message_id']) for item in new],
                                 [*range(158, 90, -1), *range(88, 0, -1)])
                self.assertEqual(scan(incremental=journal), new)
                self.assertEqual(self.transport.scan('saved', '', 50, 'all', incremental=journal), [])
                client.account = SimpleNamespace(id=722)
                other = scan(incremental=journal)
                self.assertEqual(int(other[-1]['message_id']), 109)
            finally:
                store.db.close()

    def test_preview_can_complete_before_the_next_scan_result(self):
        client = self.login()
        client.messages[5].media.document.thumbs = [PhotoSize(w=120, h=80, type='m', size=5)]
        tasks = []

        def publish(row):
            if not tasks:
                tasks.append(asyncio.create_task(self.transport._serial(self.transport._preview, row, 'media')))
            else:
                self.assertTrue(tasks[0].done())
                self.assertEqual(tasks[0].result(), b'thumbnail')

        self.transport.scan('channel', '-1000000000989', 15, 'all', on_item=publish)
        self.assertEqual(client.previews, [(5, bytes, 'm')])

    def test_forward_metadata_and_preview_recheck_original_identity(self):
        client = self.login()
        message = client.messages[3]
        message.fwd_from = SimpleNamespace(from_id=PeerChannel(channel_id=660), channel_post=7)
        message.forward = SimpleNamespace(chat=client.origin)
        message.media.document.thumbs = [PhotoSize(w=120, h=80, type='m', size=5)]
        item, = self.transport.scan('link', 't.me/example/3', 1, 'all')
        self.assertEqual((item['forward_id'], item['forward_title'], item['forward_url']),
                         ('-1000000000660', '来源频道', 'https://t.me/source/7'))
        self.assertEqual(self.transport.preview(item, 'media'), b'thumbnail')
        self.assertEqual(self.transport.preview(item, 'avatar'), b'avatar')
        self.assertEqual(client.previews, [(3, bytes, 'm'), (660, bytes, False)])
        self.assertEqual(client.files, [])
        self.assertIsNone(self.transport.preview({**item, 'forward_id': '-1000000000989'}, 'avatar'))
        message.noforwards = True
        with self.assertRaisesRegex(TelegramTransportError, '内容保护'):
            self.transport.preview(item, 'media')

    def test_preview_skips_full_photo_and_has_no_full_document_fallback(self):
        client = self.login()
        item, = self.transport.scan('link', 't.me/example/3', 1, 'all')
        self.assertIsNone(self.transport.preview(item))
        message = client.messages[3]
        full = PhotoSize(w=120, h=120, type='x', size=8)
        message.media = MessageMediaPhoto(photo=Photo(id=777, access_hash=11, file_reference=b'ref',
                                                     dc_id=2, sizes=[full]))
        item, = self.transport.scan('link', 't.me/example/3', 1, 'all')
        self.assertIsNone(self.transport.preview(item))
        message.media.photo.sizes.append(PhotoSize(w=60, h=60, type='m', size=5))
        self.assertEqual(self.transport.preview(item), b'thumbnail')
        self.assertEqual(client.previews, [(3, bytes, 'm')])
        self.assertEqual(client.files, [])

    def test_photo_uses_the_same_full_variant_for_size_dimensions_and_transfer(self):
        client = self.login()
        message = client.messages[3]
        sizes = [PhotoSize(w=5, h=5, type='s', size=4),
                 PhotoSizeProgressive(w=10, h=11, type='x', sizes=[2, 6]),
                 PhotoCachedSize(w=4, h=4, type='m', bytes=b'tiny')]
        photo = Photo(id=777, access_hash=11, file_reference=b'reference', dc_id=2, sizes=sizes)
        message.media = MessageMediaPhoto(photo=photo)
        item, = self.transport.scan('link', 't.me/example/3', 1, 'image')
        self.assertEqual((item['kind'], item['extension'], item['size'], item['width'], item['height']),
                         ('image', '.jpg', 6, 10, 11))
        self.assertEqual(b''.join(self.transport.chunks(item, 3)), b'def')
        location, size, dc = client.files[-1]
        self.assertEqual((location.id, location.thumb_size, size, dc), (777, 'x', 6, 2))
        sizes[1].w = 12
        with self.assertRaises(TelegramTransportError):
            list(self.transport.chunks(item, 3))

    def test_link_to_comment_is_not_treated_as_link_to_parent_post(self):
        self.login()
        with self.assertRaises(TelegramTransportError):
            self.transport.scan('link', 'https://t.me/example/3?comment=8', 1, 'all')

    def test_close_cancels_a_network_request_and_disconnects(self):
        client = self.login()
        started = threading.Event()
        request_errors = []

        async def slow_get_me():
            started.set()
            await asyncio.sleep(10)

        def channels():
            try:
                self.transport.channels()
            except TelegramTransportError as error:
                request_errors.append(error.code)

        with patch.object(client, 'get_me', slow_get_me):
            worker = threading.Thread(target=channels)
            worker.start()
            self.assertTrue(started.wait(2))
            closer = threading.Thread(target=self.transport.close)
            closer.start()
            closer.join(2)
            self.assertFalse(closer.is_alive())
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(request_errors, ['closed'])
        self.assertFalse(client.connected)

    def test_transfer_refreshes_reference_at_current_offset_and_checks_identity_and_protection(self):
        client = self.login()
        item, = self.transport.scan('link', 'https://t.me/example/3', 1, 'all')
        client.download_failures.append(FileReferenceExpiredError('secret file reference'))
        self.assertEqual(b''.join(self.transport.chunks(item, 1)), b'bcdef')
        self.assertEqual(client.offsets, [1, 3])
        self.assertEqual(client.requested, [3, 3, 3])
        self.assertEqual(client.downloads_closed, 2)
        client.messages[3].media.document.id = 88
        with self.assertRaises(TelegramTransportError):
            list(self.transport.chunks(item, 1))
        client.messages[3].media.document.id = 203
        client.messages[3].noforwards = True
        with self.assertRaises(TelegramTransportError):
            list(self.transport.chunks(item, 1))
        self.assertEqual(client.offsets, [1, 3])

    def test_report_flood_wait_without_raw_errors_and_reject_unsupported_proxy(self):
        client = self.login()
        item, = self.transport.scan('link', 'https://t.me/example/3', 1, 'all')
        client.download_failures.append(FloodWaitError('private demo'))
        with self.assertRaises(TelegramTransportError) as failure:
            list(self.transport.chunks(item, 0))
        self.assertEqual(failure.exception.seconds, 21)
        self.assertNotIn('private demo', str(failure.exception))
        self.proxy = 'https://synthetic:private@localhost:8080'
        with self.assertRaises(TelegramTransportError) as failure:
            self.transport.channels()
        self.assertNotIn('private', str(failure.exception))
        self.assertEqual(len(self.clients), 1)
