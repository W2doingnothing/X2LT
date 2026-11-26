from hashlib import sha256
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

from local_video.libraries import LibraryManager
from local_video.services.telegram_queue import TelegramQueue
from local_video.store import Store


class TelegramQueueTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.media = self.root / 'media'
        self.media.mkdir()
        self.store = Store(self.root / 'queue.db')
        self.addCleanup(self.store.close)
        self.libraries = LibraryManager(self.root / 'libraries.json')
        self.library = self.libraries.add('媒体库', self.media)
        self.transport = Mock()
        self.register = Mock()
        self.queue = self.create_queue()
        self.addCleanup(lambda: self.stop(self.queue))
        self.item = dict(peer_id='-1001', peer_title='test', message_id=12, media_id='photo-34',
                         group_id='', title='示例', body='', url='', kind='video', extension='.mp4',
                         size=6, width=160, height=90, name='../CON.mp4')

    def create_queue(self):
        return TelegramQueue(self.store, self.libraries, self.transport, self.register, Mock())

    def stop(self, queue):
        deadline = time.monotonic() + 4
        while not queue.close():
            if time.monotonic() > deadline:
                self.fail('队列未停止')

    def state(self, expected, queue=None):
        queue = queue or self.queue
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            rows = queue.list()
            if rows and rows[0]['state'] == expected:
                return rows[0]
            time.sleep(.01)
        self.fail(f'等待 {expected}，当前为 {queue.list()}')

    def add(self):
        return self.queue.add([self.item], self.library['id'])

    def test_success_deduplicates_and_registers_verified_file(self):
        self.transport.chunks.return_value = iter((b'abc', b'def'))
        self.assertEqual(self.add(), 1)
        self.assertEqual(self.add(), 0)
        row = self.state('completed')
        self.assertEqual((row['received'], row['total']), (6, 6))
        job, path, digest = self.register.call_args.args
        self.assertEqual(job['library_id'], self.library['id'])
        self.assertEqual(digest, sha256(b'abcdef').hexdigest())
        self.assertEqual(path.read_bytes(), b'abcdef')
        self.assertTrue(path.is_relative_to(self.media))
        self.assertEqual(list(self.media.rglob('*.part')), [])

    def test_pause_shutdown_and_resume_at_saved_offset(self):
        gate = threading.Event()
        started = threading.Event()

        def chunks(item, offset):
            if offset == 0:
                yield b'abc'
                started.set()
                gate.wait(timeout=2)
            yield b'def'

        self.transport.chunks.side_effect = chunks
        self.add()
        self.assertTrue(started.wait(2))
        self.assertFalse(self.queue.close())
        self.assertTrue(self.queue.busy())
        gate.set()
        self.stop(self.queue)
        self.assertFalse(self.queue.busy())
        self.queue = self.create_queue()
        paused = self.state('paused')
        self.assertEqual(paused['received'], 3)
        self.queue.control('resume', None)
        self.state('completed')
        self.assertEqual(self.transport.chunks.call_args.args[1], 3)

    def test_bulk_cancel_can_keep_or_delete_active_partial(self):
        for delete_partial in (False, True):
            with self.subTest(delete_partial=delete_partial):
                self.item['media_id'] = f'bulk-{delete_partial}'
                gate = threading.Event()
                started = threading.Event()

                def chunks(item, offset):
                    if offset == 0:
                        yield b'abc'
                        started.set()
                        gate.wait(timeout=2)
                    yield b'def'

                self.transport.chunks.side_effect = chunks
                self.add()
                try:
                    self.assertTrue(started.wait(2))
                    self.assertTrue(self.queue.control('cancel', delete_partial=delete_partial))
                finally:
                    gate.set()
                deadline = time.monotonic() + 4
                while self.queue.busy() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertFalse(self.queue.busy())
                row = self.state('canceled')
                self.assertEqual(row['received'], 0 if delete_partial else 3)
                self.assertEqual(len(list(self.media.rglob('*.part'))), int(not delete_partial))
                self.queue.control('retry', [row['id']])
                self.state('completed')
                self.assertEqual(self.transport.chunks.call_args.args[1], 0 if delete_partial else 3)
                self.assertEqual(len(list(self.media.rglob('*.mp4'))), 2 if delete_partial else 1)

    def test_short_transfer_retries_from_checkpoint(self):
        self.transport.chunks.side_effect = [iter((b'abc',)), iter((b'def',))]
        self.add()
        row = self.state('failed')
        self.assertEqual(row['received'], 3)
        self.register.assert_not_called()
        self.queue.control('retry', [row['id']])
        self.state('completed')
        self.assertEqual(self.transport.chunks.call_args.args[1], 3)

    def test_registration_failure_recovers_without_download(self):
        self.transport.chunks.return_value = iter((b'abcdef',))
        self.register.side_effect = [OSError('private/path'), None]
        self.add()
        row = self.state('failed')
        self.assertNotIn('private/path', row['error'])
        self.stop(self.queue)
        self.queue = self.create_queue()
        self.queue.control('retry', [row['id']])
        self.state('completed')
        self.transport.chunks.assert_called_once()
        self.assertEqual(self.register.call_count, 2)

    def test_quota_pauses_before_network_read(self):
        self.libraries.update(self.library['id'], quota_bytes=5)
        self.add()
        self.state('paused')
        self.transport.chunks.assert_not_called()

    def test_unknown_size_respects_exact_quota(self):
        self.item['size'] = 0
        self.libraries.update(self.library['id'], quota_bytes=6)
        self.transport.chunks.return_value = iter((b'abcde', b'f'))
        self.add()
        row = self.state('completed')
        self.assertEqual((row['received'], row['total']), (6, 6))

    def test_retry_after_flood_wait_uses_saved_bytes(self):
        class FloodWait(Exception):
            seconds = .4

        def interrupted():
            yield b'abc'
            raise FloodWait('private proxy')

        self.transport.chunks.side_effect = [interrupted(), iter((b'def',))]
        self.add()
        waiting = self.state('waiting')
        self.assertNotIn('private proxy', waiting['error'])
        self.assertEqual(self.transport.chunks.call_count, 1)
        self.state('completed')
        self.assertEqual(self.transport.chunks.call_args.args[1], 3)

    def test_modified_partial_requires_explicit_cancel_before_retry(self):
        self.transport.chunks.side_effect = [iter((b'abc',)), iter((b'abcdef',))]
        self.add()
        row = self.state('failed')
        part, = self.media.rglob('*.part')
        part.write_bytes(b'xxx')
        self.queue.control('retry', [row['id']])
        result = self.state('failed')
        self.assertIn('校验失败', result['error'])
        self.assertEqual(part.read_bytes(), b'xxx')
        self.queue.control('cancel', [row['id']])
        self.assertFalse(part.exists())
        self.queue.control('retry', [row['id']])
        self.state('completed')
        self.assertEqual(self.transport.chunks.call_args.args[1], 0)
