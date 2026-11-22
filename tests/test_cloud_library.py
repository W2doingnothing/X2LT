from contextlib import closing
import hashlib
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from local_video.discovery import Video
from local_video.libraries import LibraryManager
from local_video.runner import destination_for
from local_video.services.cloud import CloudService
from local_video.services.runtime import WorkerRuntime
from local_video.store import Store


class FakeDrive:
    def __init__(self):
        self.account = 'first'
        self.files = {'A': ('', '假想视频.mp4', b'cloud video')}
        self.dirs = {}
        self.results = {}
        self.fail_once = False

    def status(self):
        return {'logged_in': True, 'account_id': self.account}

    def list(self, parent, page):
        items = [dict(id=key, name=name, parent_id=folder, type='file', size=len(data))
                 for key, (folder, name, data) in self.files.items() if folder == parent]
        return {'items': items if page == 0 else [], 'has_more': False}

    def mkdir(self, parent, name, **kwargs):
        key = f'D{len(self.dirs)}'
        self.dirs[key] = parent, name
        return key

    def submit(self, op, request_id, task_id):
        if self.fail_once:
            self.fail_once = False
            self.paused_request = request_id
            raise ValueError('Connection interrupted')
        if op['type'] == 'upload':
            path = Path(op['local_path'])
            key = f'F{len(self.files)}'
            self.files[key] = op['remote_parent_id'], path.name, path.read_bytes()
            result = {'file': {'fileId': key}}
        else:
            _, name, data = self.files[op['remote_id']]
            (Path(op['local_dir']) / name).write_bytes(data)
            result = {'downloaded_files': 1}
        self.results[request_id] = {'code': 'OK', 'batch_id': request_id, 'batch_state': 'SUCCEEDED',
                                    'data': {'items': [{'operation_id': 'transfer', 'state': 'SUCCEEDED', 'result': result}]}}
        return self.results[request_id]

    def batch(self, batch):
        return self.results[batch]


class CloudTests(unittest.TestCase):
    def run_job(self, service, expected='completed'):
        until = time.monotonic() + 10
        while service.worker and time.monotonic() < until:
            time.sleep(.01)
        self.assertIsNone(service.worker)
        job = service.jobs({})['items'][0]
        self.assertEqual(job['state'], expected, job)
        return job

    def test_folder_request_is_valid_and_failed_job_retries_same_ids(self):
        with tempfile.TemporaryDirectory() as temp, closing(Store(Path(temp)/'queue.db')) as store:
            root = Path(temp).resolve()
            provider = FakeDrive()
            service = CloudService(root, store, LibraryManager(root/'libraries.json'), None, WorkerRuntime(), provider)
            provider.mkdir = Mock(side_effect=[ValueError('temporary failure'), 'folder-saved'])
            with self.assertRaisesRegex(ValueError, 'temporary'):
                service.folder('first', '', 'X2LT归档')
            self.assertEqual(service.folder('first', '', 'X2LT归档'), 'folder-saved')
            first, second = provider.mkdir.call_args_list
            self.assertEqual(first, second)
            self.assertEqual({UUID(value).version for value in first.kwargs.values()}, {4})
            self.assertEqual(service.folder('first', '', 'X2LT归档'), 'folder-saved')
            self.assertEqual(provider.mkdir.call_count, 2)

    def test_cancel_active_batch_without_duplicate_upload_and_retry_old_preflight_failure(self):
        with tempfile.TemporaryDirectory() as temp, closing(Store(Path(temp)/'queue.db')) as store:
            data = Path(temp).resolve()
            root = data/'library'
            root.mkdir()
            libraries = LibraryManager(data/'libraries.json')
            library = libraries.add('收藏', root)
            provider = FakeDrive()
            original_submit = provider.submit
            service = CloudService(data, store, libraries, None, WorkerRuntime(), provider)
            job = store.enqueue(Video('777','2','alice','https://video.twimg.com/1.mp4',
                                      'https://x.com/alice/status/777'), root, 'best')
            payload = b'cancel fixture'
            store.set_state(job, 'completed', bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
            path = destination_for(store.get_many([job])[0])
            path.parent.mkdir(parents=True)
            path.write_bytes(payload)
            provider.mkdir = Mock(side_effect=ValueError('first folder request failed'))
            options = dict(library_id=library['id'], parent='', mode='source', threshold=2)
            service.archive_start({'id': service.archive_plan(options)['id']})
            failed = self.run_job(service, 'failed')
            mkdir_request = provider.mkdir.call_args.kwargs
            provider.mkdir.side_effect = None
            provider.mkdir.return_value = 'resolved-folder'
            # Retry the persisted task rather than build another media request.
            submitted, release = Event(), Event()
            def delayed_submit(*args):
                submitted.set()
                if not release.wait(4):
                    raise ValueError('test did not allow submission to return')
                return {'code':'OK', 'batch_id':'active', 'batch_state':'RUNNING'}
            provider.submit = Mock(side_effect=delayed_submit)
            provider.stop = Mock(return_value={'code':'USER_STOPPED','batch_state':'STOPPED'})
            self.addCleanup(release.set)
            self.assertEqual(service.retry({'id':failed['id']})['count'], 1)
            try:
                self.assertTrue(submitted.wait(4))
                self.assertEqual(provider.mkdir.call_args_list[1].kwargs, mkdir_request)
                duplicate = service.archive_start({'id': service.archive_plan(options)['id']})
                self.assertEqual(duplicate['queued'], 0)
                self.assertEqual(service.cancel_jobs({'id':failed['id']})['count'], 1)
            finally:
                release.set()
            self.run_job(service, 'cancelled')
            provider.stop.assert_called_once_with('active')
            provider.submit.assert_called_once()
            self.assertTrue(path.exists())
            self.assertEqual(len(provider.files), 1)
            provider.submit = original_submit

    def test_library_archive_uses_previewed_layout_and_skips_existing_cloud_copy(self):
        with tempfile.TemporaryDirectory() as temp, closing(Store(Path(temp)/'queue.db')) as store:
            data = Path(temp).resolve()
            root = data/'library'
            root.mkdir()
            libraries = LibraryManager(data/'libraries.json')
            library = libraries.add('收藏', root)
            provider = FakeDrive()
            service = CloudService(data, store, libraries, None, WorkerRuntime(), provider)
            job = store.enqueue(Video('123','4','alice','https://video.twimg.com/1.mp4',
                                      'https://x.com/alice/status/123'), root, 'best')
            content = b'archive fixture'
            store.set_state(job, 'completed', bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
            path = destination_for(store.get_many([job])[0])
            path.parent.mkdir(parents=True)
            path.write_bytes(content)
            store.catalog.set_tags('123', ['旅行'])
            options = dict(library_id=library['id'], parent='', mode='source', threshold=2)
            with self.assertRaisesRegex(ValueError, '请选择'):
                service.archive_plan(dict(options, ids=[]))
            plan = service.archive_plan(options)
            self.assertEqual(plan['folders'][0]['path'][-2:], ['推特', '其他博主'])
            self.assertEqual(plan['queued_count'], 1)
            self.assertEqual(service.archive_start({'id': plan['id']})['queued'], 1)
            self.run_job(service)
            with self.assertRaisesRegex(ValueError, '预览已失效'):
                service.archive_start({'id': plan['id']})
            parent, remote_name, uploaded = provider.files['F1']
            self.assertEqual(provider.dirs[parent][1], '其他博主')
            self.assertRegex(remote_name, r'^[a-f0-9]{32}\.mp4$')
            self.assertEqual(uploaded, content)

            changed = service.archive_plan({**options, 'mode': 'tag'})
            self.assertEqual(changed['folders'][0]['path'][-1], '旅行')
            self.assertEqual(changed['known_count'], 1)
            service.archive_start({'id': changed['id']})
            self.run_job(service)
            self.assertEqual(len(provider.files), 2)
            self.assertNotIn('旅行', [name for _, name in provider.dirs.values()])

            missing = service.archive_plan({**options, 'mode': 'tag'})
            provider.files.pop('F1')
            service.archive_start({'id': missing['id']})
            self.run_job(service)
            self.assertIn('旅行', [name for _, name in provider.dirs.values()])

    def test_import_link_tag_upload_release_and_restore(self):
        with tempfile.TemporaryDirectory() as temp, closing(Store(Path(temp)/'queue.db')) as store:
            data = Path(temp).resolve()
            root = data/'first'; root.mkdir()
            second = data/'second'; second.mkdir()
            libraries = LibraryManager(data/'libraries.json')
            library = libraries.add('私人库', root)
            other = libraries.add('另一库', second)
            provider = FakeDrive()
            service = CloudService(data, store, libraries, None, WorkerRuntime(), provider)
            service.import_folder({'parent': ''})
            self.run_job(service)
            imported = service.catalog_page({})['items'][0]
            service.tags({'id': imported['id'], 'tags': ['旅行']})
            service.link({'id': imported['id'], 'library_id': other['id'], 'linked': True})
            service.import_folder({'parent': ''})
            self.run_job(service)
            self.assertEqual(service.catalog_page({'library_id': other['id'], 'tag': '旅行'})['total'], 1)
            self.assertEqual(service.catalog_page({'query': '假想'})['total'], 1)

            job = store.enqueue(Video('123','4','alice','https://video.twimg.com/1.mp4',
                                      'https://x.com/alice/status/123',tweet_text='原始推文正文'), root, 'best')
            store.set_state(job, 'completed', bytes=6)
            path = destination_for(store.get_many([job])[0])
            path.parent.mkdir(parents=True)
            path.write_bytes(b'source')
            store.catalog.set_tags('123', ['保留'])
            provider.fail_once = True
            service.upload({'ids':[job], 'parent':''})
            paused = self.run_job(service, 'paused')
            service.retry({'id': paused['id']})
            self.run_job(service)
            self.assertIn(provider.paused_request, provider.results)
            self.assertEqual(len(provider.dirs), 4)
            remote_parent, remote_name, data_bytes = provider.files['F1']
            self.assertEqual(data_bytes, b'source')
            self.assertRegex(remote_name, r'^[0-9a-f]{32}\.mp4$')
            self.assertEqual(provider.dirs[remote_parent][1], time.strftime('%m'))
            entry = service.positions({'ids':[job]})[str(job)]
            self.assertEqual(entry['tags'], ['保留'])
            self.assertEqual([location['kind'] for location in entry['locations']], ['local','cloud'])
            local, cloud = (loc['id'] for loc in entry['locations'])
            self.assertEqual(service.locate({'id':cloud}), {'parent':remote_parent})
            self.assertEqual(service.catalog_page({})['total'], 2)

            provider.files['F1'] = remote_parent, remote_name, b'edited'
            with patch('local_video.services.cloud.recycle_file') as recycle:
                service.release({'id': local})
                self.run_job(service, 'failed')
                recycle.assert_not_called()
            self.assertTrue(path.exists())
            provider.files['F1'] = remote_parent, remote_name, b'source'
            with patch('local_video.services.cloud.recycle_file', lambda p: p.unlink()):
                service.release({'id': local})
                self.run_job(service)
            self.assertFalse(path.exists())
            self.assertEqual([loc['kind'] for loc in service.positions({'ids':[job]})[str(job)]['locations']], ['cloud'])
            self.assertEqual(store.get_many([job])[0]['state'], 'completed')
            service.download({'id':cloud, 'library_id':other['id']})
            self.run_job(service)
            entry = service.positions({'ids':[job]})[str(job)]
            self.assertEqual(entry['text'], '原始推文正文')
            self.assertEqual(entry['filename'], path.name)
            self.assertEqual({l['kind'] for l in entry['locations']}, {'local','cloud'})
            result_count = len(provider.results)
            service.download({'id':cloud, 'library_id':other['id']})
            self.run_job(service)
            self.assertEqual(len(provider.results), result_count)

            backup = data/'index.json'
            service.native = Mock()
            service.native.choose_cloud_backup.return_value = backup
            service.backup({})
            saved = backup.read_text(encoding='utf8')
            self.assertIn('原始推文正文', saved)
            self.assertNotIn('video.twimg.com', saved)
            with closing(Store(data/'restored.db')) as restored_store:
                restored = CloudService(data, restored_store, libraries, service.native, WorkerRuntime(), provider)
                restored.restore({})
                recovered = restored.catalog_page({'query': '原始推文正文'})['items'][0]
                self.assertEqual(recovered['tags'], ['保留'])
                self.assertEqual(recovered['locations'][0]['state'], 'unconfirmed')
                restored.open_source({'id': recovered['id']})
                service.native.open_url.assert_called_with('https://x.com/i/status/123')
            provider.account = 'other'
            with self.assertRaisesRegex(ValueError, '所属'):
                service.download({'id':cloud, 'library_id':other['id']})
