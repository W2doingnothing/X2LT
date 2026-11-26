from types import SimpleNamespace
import asyncio
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


class FloodWaitError(Exception):
    seconds = 21


class FileReferenceExpiredError(Exception):
    pass


class Session:
    def __init__(self, value=''):
        self.value = value

    def save(self):
        return 'synthetic-session'


class Client:
    def __init__(self, session, *args, **kwargs):
        self.session = session
        self.options = kwargs
        self.loop = asyncio.get_running_loop()
        self.connected = False
        self.authorized = bool(session.value)
        self.peer = Channel(id=989, username='example', title='测试频道')
        self.messages = {3: make_message(3), 4: make_message(4, group=700), 5: make_message(5, group=700),
                         6: make_message(6, noforwards=True)}
        self.offsets = []
        self.files = []
        self.requested = []
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
        return object() if self.authorized else None

    async def send_code_request(self, phone):
        return SimpleNamespace(phone_code_hash='synthetic-code-hash')

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
        assert source in ('example', -1000000000989), source
        return self.peer

    async def get_messages(self, peer, *, ids):
        self.requested.append(ids)
        if isinstance(ids, int):
            return self.messages.get(ids)
        return [self.messages.get(message_id) for message_id in ids]

    async def iter_messages(self, peer, *, limit):
        for message in list(self.messages.values())[:limit]:
            yield message

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
                                  (Channel, Document, MessageMediaDocument, MessageMediaPhoto, Photo,
                                   PhotoSize, PhotoSizeProgressive, PhotoCachedSize, InputPhotoFileLocation)})
        utils = SimpleNamespace(get_peer_id=lambda entity: -(10**12 + entity.id))
        errors = SimpleNamespace(**{kind.__name__: kind for kind in
                                   (SessionPasswordNeededError, FloodWaitError, FileReferenceExpiredError)})
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

    def test_scan_album_private_link_and_skip_protected_or_external_media(self):
        client = self.login()
        album = self.transport.scan('link', 'https://t.me/c/989/40/4?single', 15, 'all')
        self.assertEqual([row['message_id'] for row in album], ['4', '5'])
        self.assertEqual(album[0]['media_id'], '204')
        self.assertEqual(album[0]['group_id'], '700')
        self.assertEqual(album[0]['size'], 6)
        self.assertEqual(album[0]['body'], '示例\n正文')
        self.assertEqual(album[0]['url'], 'https://t.me/example/4')
        scanned = self.transport.scan('channel', '-1000000000989', 15, 'video')
        self.assertEqual([row['message_id'] for row in scanned], ['3', '4', '5'])
        self.assertEqual(self.transport.scan('channel', '-1000000000989', 15, 'image'), [])
        with self.assertRaises(TelegramTransportError):
            self.transport.scan('link', 'https://t.me.evil.test/example/4', 15, 'all')
        with self.assertRaisesRegex(TelegramTransportError, '内容保护'):
            self.transport.scan('link', 'https://t.me/example/6', 15, 'all')
        client.peer.noforwards = True
        with self.assertRaisesRegex(TelegramTransportError, '内容保护'):
            self.transport.scan('channel', '-1000000000989', 15, 'all')

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
