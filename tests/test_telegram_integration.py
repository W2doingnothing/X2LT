from base64 import b64decode
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from unittest import TestCase
from unittest.mock import Mock
from PIL import Image

from local_video.archive import comparable_path
from local_video.desktop_service import DesktopService
from local_video.services.telegram import read_api_credentials
from local_video.services.random_feed import RandomFeed


class TelegramIntegrationTests(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = comparable_path(Path(tmp.name).resolve(strict=True))
        self.app = DesktopService(self.root/'data', self.root/'home')
        self.addCleanup(self.app._close)

    def test_credentials_are_imported_only_through_native_selection(self):
        file = self.root/'test.txt'
        file.write_text('api_id: 123456\napi_hash: ' + 'a'*32, encoding='utf8')
        self.assertEqual(read_api_credentials(file), (123456, 'a'*32))
        self.assertFalse(self.app.call('tg_import', {'path': str(file)})['ok'])
        self.app._telegram._choose_credentials = Mock(return_value=file)
        response = self.app.call('tg_import')
        self.assertTrue(response['ok'], response)
        self.assertTrue(response['data']['configured'])
        self.assertNotIn('a'*32, str(response))
        self.assertNotIn('a'*32, (self.root/'data'/'telegram-session.dpapi').read_bytes().decode('latin1'))

    def call(self, action, payload=None):
        result = self.app.call(action, payload)
        self.assertTrue(result['ok'], result)
        return result['data']

    def test_historical_backfill_streams_over_scan_cap_and_deduplicates(self):
        service = self.app._telegram
        library = self.app._libraries.get()['id']
        service._transport.status = Mock(return_value={'authorized': True})
        service._queue._wake = Mock()

        def scan(mode, source, limit, media_mode, *, on_item, **options):
            self.assertEqual((mode, source, limit), ('saved', '', 0))
            self.assertIsNotNone(options['incremental'])
            for message in range(1, 2101):
                on_item(dict(peer_id='saved:1', message_id=str(message), media_id='media',
                             kind='video', extension='mp4', size=100, title='Sample'))
            return []

        service._transport.scan = Mock(side_effect=scan)
        params = dict(mode='saved', source='', limit=50, media_mode='all', library_id=library, backfill=True)
        for expected in (2100, 0):
            self.call('tg_scan', params)
            deadline = time.monotonic() + 8
            status = self.call('tg_backfill_status')
            while status['state'] == 'scanning' and time.monotonic() < deadline:
                time.sleep(.01)
                status = self.call('tg_backfill_status')
            self.assertEqual((status['state'], status['found'], status['added']), ('ready', 2100, expected))
        self.assertEqual(len(service._queue.list()), 2100)

    def test_qr_bridge_only_exposes_local_image_and_status(self):
        transport = self.app._telegram._transport
        state = {'state': 'waiting', 'expires_at': 1234567890000}
        transport.qr_start = Mock(return_value={**state, 'url': 'tg://login?token=synthetic_QR'})
        transport.qr_status = Mock(return_value=state)
        transport.qr_cancel = Mock(return_value={'state': 'idle'})
        response = self.call('tg_qr_start')
        self.assertNotIn('url', response)
        self.assertTrue(response['image'].startswith('data:image/png;base64,'))
        with Image.open(BytesIO(b64decode(response['image'].split(',')[1]))) as rendered:
            self.assertEqual(rendered.format, 'PNG')
            self.assertEqual(rendered.width, rendered.height)
        self.assertEqual(self.call('tg_qr_status'), state)
        self.assertEqual(self.call('tg_qr_cancel'), {'state': 'idle'})
        transport.qr_cancel.assert_called_once()

    def test_manual_credentials_use_the_same_vault_and_do_not_echo_secrets(self):
        credentials = {'api_id': '123456', 'api_hash': 'b'*32}
        self.assertTrue(self.call('tg_credentials', credentials)['configured'])
        self.assertNotIn(credentials['api_hash'], str(self.call('tg_status')))
        self.assertNotIn(credentials['api_hash'], (self.root/'data'/'telegram-session.dpapi').read_bytes().decode('latin1'))
        self.assertFalse(self.app.call('tg_credentials', {**credentials, 'api_hash': 'invalid'})['ok'])
        self.assertTrue(self.call('tg_status')['configured'])

    def test_proxy_modes_and_legacy_settings(self):
        service = self.app._telegram
        self.app._settings_service.credentials['proxy'] = 'socks5h://127.0.0.1:1234'
        service._proxy_vault.save({'url': 'http://fixture:secret@proxy.invalid:8080'})
        status = self.call('tg_status')
        self.assertEqual(status['proxy_mode'], 'existing')
        self.assertNotIn('secret', str(status))
        self.assertEqual(self.call('tg_proxy', {'mode': 'socks5', 'port': 1080})['proxy_port'], 1080)
        self.assertEqual(service._effective_proxy(), 'socks5h://127.0.0.1:1080')
        self.assertFalse(self.app.call('tg_proxy', {'mode': 'http', 'port': 65536})['ok'])
        self.assertEqual(self.call('tg_proxy', {'mode': 'direct'})['proxy_mode'], 'direct')
        self.assertEqual(service._effective_proxy(), '')
        self.assertEqual(self.call('tg_proxy', {'mode': 'inherit'})['proxy_mode'], 'inherit')
        self.assertEqual(service._effective_proxy(), 'socks5h://127.0.0.1:1234')

    def until(self, condition):
        for _ in range(200):
            if value := condition():
                return value
            time.sleep(.025)
        self.fail('Telegram background operation timed out')

    def test_channel_date_range_exposes_progress_status_and_cached_thumbnail(self):
        transport = self.app._telegram._transport
        transport.status = Mock(return_value={'configured': True, 'authorized': True})
        asset = BytesIO()
        Image.new('RGB', (16, 12), 'blue').save(asset, format='JPEG')
        transport.preview = Mock(return_value=asset.getvalue())
        item = dict(peer_id='-1000000000042', peer_title='合成频道', message_id='8', media_id='21',
                    title='合成标题', body='正文', kind='image', size=100, sent_at=1234,
                    forward_id='-1000000000060', forward_title='原频道')
        def scan(mode, source, limit, media_mode, *, cancelled, start_at, end_at, on_item):
            self.assertEqual(datetime.fromtimestamp(start_at), datetime(2026, 11, 1))
            self.assertEqual(datetime.fromtimestamp(end_at), datetime(2026, 11, 4))
            on_item(item)
            return [item]
        transport.scan = Mock(side_effect=scan)
        opts = dict(mode='channel', source=item['peer_id'], limit=50, media_mode='all',
                    start_date='2026-11-01', end_date='2026-11-03')
        self.assertFalse(self.app.call('tg_scan', {**opts, 'end_date': '2026-10-01'})['ok'])
        scan_id = self.call('tg_scan', opts)['id']
        library = self.app._libraries.get()['id']
        rows = self.until(lambda: self.call('tg_scan_status', {'id': scan_id, 'library_id': library})['items'])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['download_state'], 'new')
        self.assertEqual(rows[0]['forward_title'], '原频道')
        self.assertEqual(rows[0]['message_key'], '-1000000000042:8')
        preview = {'scan_id': scan_id, 'id': rows[0]['id']}
        self.assertTrue(self.call('tg_scan_preview', preview).startswith('data:image/jpeg;base64,'))
        self.assertTrue(self.call('tg_scan_preview', preview).startswith('data:image/jpeg;base64,'))
        transport.preview.assert_called_once()

    def test_cancel_preserves_scan_results(self):
        transport = self.app._telegram._transport
        item = dict(peer_id='-1000000000042', peer_title='合成频道', message_id='8',
                    media_id='21', title='合成标题', kind='image', size=5)

        def scan(*args, cancelled, on_item, **kwargs):
            on_item(item)
            for _ in range(100):
                if cancelled():
                    return []
                time.sleep(.01)
            return []

        transport.scan = Mock(side_effect=scan)
        identifier = self.call('tg_scan', dict(mode='channel', source=item['peer_id'], limit=50, media_mode='all'))['id']
        query = {'id': identifier}
        self.until(lambda: self.call('tg_scan_status', query)['items'])
        self.assertEqual(self.call('tg_scan_status', query)['state'], 'scanning')
        self.call('tg_scan_cancel', query)
        result = self.call('tg_scan_status', query)
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(len(result['items']), 1)

    def test_saved_incremental_scan_preserves_canceled_download_state(self):
        service = self.app._telegram
        item = dict(peer_id='saved:91', peer_title='收藏夹', message_id='8', media_id='21',
                    title='合成标题', kind='video', extension='.mp4', size=5, url='')
        service._transport.scan = Mock(return_value=[item])
        library = self.app._libraries.get()['id']
        service._queue._wake = Mock()
        service._queue.add([item], library)
        job, = self.call('tg_jobs')
        self.call('tg_control', {'action': 'cancel', 'ids': [job['id']]})
        options = dict(mode='saved', source='', limit=50, media_mode='image',
                       start_date='2026-11-01', end_date='2026-11-03')
        self.assertFalse(self.app.call('tg_scan', {**options, 'source': 'someone_else'})['ok'])
        scan = self.call('tg_scan', {**options, 'incremental': True})['id']
        query = {'id': scan, 'library_id': library}
        rows = self.until(lambda: self.call('tg_scan_status', query)['items'])
        self.assertEqual(rows[0]['download_state'], 'canceled')
        self.assertEqual(rows[0]['kind'], 'video')
        self.assertEqual(service._transport.scan.call_args.kwargs['incremental'], service._discovery)
        self.assertEqual(self.call('tg_jobs'), [])
        scan = self.call('tg_scan', options)['id']
        rows = self.until(lambda: self.call('tg_scan_status', {**query, 'id': scan})['items'])
        self.assertEqual(rows[0]['download_state'], 'new')

    def test_scan_enqueue_dedup_message_cards_and_local_playback(self):
        transport = self.app._telegram._transport
        transport.status = Mock(return_value={'configured': True, 'authorized': True})
        payload = b'\x00\x00\x00\x18ftypmp42' + b'\0'*128
        messages = [
            dict(peer_id='-1000000000042', peer_title='合成频道', message_id=str(i), media_id=str(20+i),
                 group_id='42' if i < 3 else '', title='合成标题', body='合成消息' if i == 1 else '',
                 url=f'https://t.me/demo/{i}', kind='video', extension='.mp4', size=len(payload)+1,
                 width=320, height=180, name='fixture.mp4', sent_at=1234+i,
                 forward_id='-1000000000060' if i == 2 else '',
                 forward_title='原频道' if i == 2 else '',
                 forward_url='https://t.me/origin/4' if i == 2 else '')
            for i in (3,2,1)
        ]
        transport.scan = Mock(return_value=messages)
        transport.chunks = Mock(side_effect=lambda item, offset: iter([(payload + bytes([int(item['message_id'])]))[offset:]]))
        library = self.app._libraries.get()['id']
        options = dict(mode='channel', source='-1000000000042', limit=50, media_mode='all')
        scan = self.call('tg_scan', options)['id']
        items = self.until(lambda: self.call('tg_scan_status', {'id': scan})['items'])
        self.assertEqual(len(items), 3)
        self.assertNotEqual(items[1]['message_key'], items[2]['message_key'])
        self.assertEqual(items[1]['card_key'], items[2]['card_key'])
        self.assertNotEqual(items[0]['card_key'], items[2]['card_key'])
        self.assertNotIn('peer_id', items[0])
        request = dict(scan_id=scan, ids=[item['id'] for item in items], library_id=library)
        self.assertEqual(self.call('tg_enqueue', request), 3)
        self.assertEqual(self.call('tg_enqueue', request), 0)
        self.until(lambda: len([j for j in self.call('tg_jobs') if j['state'] == 'completed']) == 3)
        removed = self.call('tg_jobs')[0]
        self.assertEqual(self.call('tg_control', {'action': 'delete', 'ids': [removed['id']], 'report': True}),
                         [{'library_id': library, 'media_key': removed['media_key']}])
        self.assertEqual(len(self.call('tg_jobs')), 2)
        self.app._telegram._queue._write('DELETE FROM telegram_downloads')
        another = self.call('tg_scan', options)['id']
        repeated = self.until(lambda: self.call('tg_scan_status', {'id': another, 'library_id': library})['items'])
        self.assertEqual({item['download_state'] for item in repeated}, {'completed'})
        self.assertEqual(self.call('tg_enqueue', {
            'scan_id': another, 'ids': [item['id'] for item in repeated], 'library_id': library,
        }), 0)
        page = self.call('local_library', {'library_id': library})
        self.assertEqual(page['total'], 2)
        self.assertTrue(all('group' not in card for card in page['items']))
        post = next(card for card in page['items'] if card['body'] == '合成消息')
        single = next(card for card in page['items'] if card is not post)
        self.assertEqual(len(post['telegram_items']), 2)
        self.assertIsNone(single['telegram']['forward'])
        self.assertEqual(post['telegram']['forward']['title'], '原频道')
        self.assertEqual(RandomFeed(self.app._store, self.app._libraries, [library]).step('current')['count'], 3)
        resource = next(r for r in self.call('resources')['resources'] if r['media_id'] == '1:21')
        self.assertEqual(resource['platform'], 'telegram')
        location = resource['locations'][0]
        self.assertIn(location['local_entry'], {item['id'] for item in post['telegram_items']})
        ticket = self.app._playback.issue({'source': 'local', 'id': location['local_entry'],
                                           'location_id': location['local_location']},
                                          'http://127.0.0.1:20000/index.html')
        self.assertTrue(ticket['url'].startswith('http://127.0.0.1:'))
        self.call('local_media_tags', {'id': location['local_entry'], 'tags': ['测试']})
        self.assertEqual(self.call('resources', {'tags': ['测试']})['total'], 1)
        old = Path(self.app._libraries.get(library)['path'])
        moved = self.root/'moved-library'
        old.rename(moved)
        self.app._native.approved_paths.add(str(comparable_path(moved.resolve(strict=True))))
        self.call('library_update', {'id': library, 'path': str(moved)})
        self.assertEqual(self.call('resources')['total'], 3)
        self.assertEqual(self.call('local_library', {'library_id': library})['items'][0]['locations'][0]['state'],
                         'available')
        self.call('local_media_remove', {'id': location['local_entry'], 'library_id': library})
        self.assertEqual(self.call('resources')['total'], 2)
