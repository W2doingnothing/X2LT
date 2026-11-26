from pathlib import Path
from tempfile import TemporaryDirectory
import time
from unittest import TestCase
from unittest.mock import Mock

from local_video.desktop_service import DesktopService
from local_video.services.telegram import read_api_credentials
from local_video.services.random_feed import RandomFeed


class TelegramIntegrationTests(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
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

    def until(self, condition):
        for _ in range(200):
            if value := condition():
                return value
            time.sleep(.025)
        self.fail('Telegram background operation timed out')

    def test_scan_enqueue_dedup_album_and_local_playback(self):
        transport = self.app._telegram._transport
        transport.status = Mock(return_value={'configured': True, 'authorized': True})
        payload = b'\x00\x00\x00\x18ftypmp42' + b'\0'*128
        messages = [
            dict(peer_id='-1000000000042', peer_title='合成频道', message_id=str(i), media_id=str(20+i),
                 group_id='42', title='合成标题', body='合成相册' if i == 1 else '',
                 url=f'https://t.me/demo/{i}', kind='video', extension='.mp4', size=len(payload)+1,
                 width=320, height=180, name='fixture.mp4')
            for i in (3,2,1)
        ]
        transport.scan = Mock(return_value=messages)
        transport.chunks = Mock(side_effect=lambda item, offset: iter([(payload + bytes([int(item['message_id'])]))[offset:]]))
        library = self.app._libraries.get()['id']
        options = dict(mode='channel', source='-1000000000042', limit=50, media_mode='all')
        scan = self.call('tg_scan', options)['id']
        items = self.until(lambda: self.call('tg_scan_status', {'id': scan})['items'])
        self.assertEqual(len(items), 3)
        self.assertNotIn('peer_id', items[0])
        request = dict(scan_id=scan, ids=[item['id'] for item in items], library_id=library)
        self.assertEqual(self.call('tg_enqueue', request), 3)
        self.assertEqual(self.call('tg_enqueue', request), 0)
        self.until(lambda: len([j for j in self.call('tg_jobs') if j['state'] == 'completed']) == 3)
        another = self.call('tg_scan', options)['id']
        repeated = self.until(lambda: self.call('tg_scan_status', {'id': another})['items'])
        self.assertEqual(self.call('tg_enqueue', {
            'scan_id': another, 'ids': [item['id'] for item in repeated], 'library_id': library,
        }), 0)
        page = self.call('local_library', {'library_id': library})
        self.assertEqual(page['total'], 1)
        card = page['items'][0]
        self.assertEqual(card['telegram']['channel'], '合成频道')
        self.assertEqual(card['group']['body'], '合成相册')
        self.assertEqual(len(card['group']['items']), 3)
        self.assertEqual(RandomFeed(self.app._store, self.app._libraries, [library]).step('current')['count'], 3)
        resource = next(r for r in self.call('resources')['resources'] if r['media_id'] == '1:21')
        self.assertEqual(resource['platform'], 'telegram')
        location = resource['locations'][0]
        self.assertEqual(card['group']['items'][0]['id'], location['local_entry'])
        ticket = self.app._playback.issue({'source': 'local', 'id': location['local_entry'],
                                           'location_id': location['local_location']},
                                          'http://127.0.0.1:20000/index.html')
        self.assertTrue(ticket['url'].startswith('http://127.0.0.1:'))
        self.call('local_media_tags', {'id': location['local_entry'], 'tags': ['测试']})
        self.assertEqual(self.call('resources', {'tags': ['测试']})['total'], 1)
        old = Path(self.app._libraries.get(library)['path'])
        moved = self.root/'moved-library'
        old.rename(moved)
        self.app._native.approved_paths.add(str(moved))
        self.call('library_update', {'id': library, 'path': str(moved)})
        self.assertEqual(self.call('resources')['total'], 3)
        self.assertEqual(self.call('local_library', {'library_id': library})['items'][0]['locations'][0]['state'],
                         'available')
        self.call('local_media_remove', {'id': location['local_entry'], 'library_id': library})
        self.assertEqual(self.call('resources')['total'], 2)
