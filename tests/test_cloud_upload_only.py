from pathlib import Path
import hashlib
import tempfile
import time
import unittest
from unittest.mock import Mock

from local_video.libraries import LibraryManager
from local_video.services.cloud import CloudService
from local_video.services.runtime import WorkerRuntime
from local_video.store import Store

from test_cloud_library import FakeDrive


class UploadOnlyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.store = Store(self.root/'queue.sqlite')
        self.addCleanup(self.store.close)
        self.native = Mock()
        self.drive = FakeDrive()
        self.service = CloudService(self.root, self.store, LibraryManager(self.root/'libraries.json'),
                                    self.native, WorkerRuntime(), self.drive, upload_only=True)
        self.addCleanup(self.service.close)

    def select(self, *paths):
        self.native.choose_upload_files.return_value = tuple(paths)
        return self.service.pick_upload({})

    def wait(self):
        deadline = time.monotonic() + 6
        while self.service.worker is not None and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertIsNone(self.service.worker)

    def test_native_selection_requires_confirmation_and_preserves_hidden_filename(self):
        source = self.root/'my private report.txt'
        source.write_bytes(b'synthetic')
        selected = self.select(source)
        self.assertEqual(selected['bytes'],9)
        self.assertEqual(selected['names'],[source.name])
        self.assertNotIn(str(self.root),str(selected))
        self.assertEqual(self.service.jobs({})['items'],[])
        result = self.service.start_upload({'id':selected['id']})
        self.assertEqual(result,{'count':1})
        self.wait()
        self.assertEqual(self.service.jobs({})['items'][0]['state'],'completed')
        folder, name, content = self.drive.files['F1']
        self.assertEqual(self.drive.dirs[folder],('','X2LT上传'))
        self.assertRegex(name,r'^[a-f0-9]{32}\.txt$')
        self.assertEqual(content,source.read_bytes())
        alias = self.root/'renamed.txt'
        alias.write_bytes(source.read_bytes())
        self.service.start_upload({'id':self.select(alias)['id']})
        self.wait()
        self.assertEqual(len(self.drive.files),2)
        self.assertIn('重复上传',self.service.jobs({})['items'][0]['message'])

    def test_legacy_non_upload_tasks_are_not_visible_or_executed(self):
        for kind in ('import','download','release'):
            self.service.catalog.write(
                """INSERT INTO cloud_jobs(id,kind,state,name,payload,updated) VALUES(?,?,'queued','old','{}',0)""",
                (kind,kind))
        self.assertEqual(self.service.jobs({})['counts'],{'cancellable':0,'retryable':0})
        with self.assertRaises(ValueError):
            self.service.cancel_jobs({'all':True})
        path = self.root/'selected'
        path.write_bytes(b'data')
        self.service.start_upload({'id':self.select(path)['id']})
        self.wait()
        self.assertEqual(self.drive.files['F1'][1].count('.'),0)
        self.assertEqual(self.service.catalog.one("SELECT COUNT(*) n FROM cloud_jobs WHERE state='queued'")['n'],3)

    def test_changed_file_requires_reselection(self):
        path = self.root/'changed.txt'
        path.write_bytes(b'first')
        selected = self.select(path)
        path.write_bytes(b'not the same')
        with self.assertRaisesRegex(ValueError,'变化'):
            self.service.start_upload({'id':selected['id']})
        self.assertEqual(self.service.jobs({})['items'],[])

    def test_existing_legacy_receipt_is_verified_before_skipping(self):
        data = self.drive.files['A'][2]
        self.service.catalog.write("""
            INSERT INTO cloud_files(variant_id,account,remote,parent,name,filename,bytes,sha256)
            VALUES(1,'first','A','','假想视频.mp4','original.mp4',?,?)
            """,(len(data),hashlib.sha256(data).hexdigest()))
        local = self.root/'saved elsewhere.mp4'
        local.write_bytes(data)
        self.service.start_upload({'id':self.select(local)['id']})
        self.wait()
        self.assertIn('重复上传',self.service.jobs({})['items'][0]['message'])
        del self.drive.files['A']
        self.service.start_upload({'id':self.select(local)['id']})
        self.wait()
        self.assertEqual(len(self.drive.files),1)
        self.assertEqual(self.service.jobs({})['items'][0]['state'],'completed')

    def test_recovery_after_receipt_and_stage_cleanup_uses_original_result(self):
        source = self.root/'interrupted.txt'
        source.write_bytes(b'interrupted')
        self.service.start_upload({'id':self.select(source)['id']})
        self.wait()
        first = self.service.jobs({})['items'][0]
        self.assertEqual(first['state'],'completed')
        source.unlink()
        self.service.catalog.write("UPDATE cloud_jobs SET state='paused' WHERE id=?", (first['id'],))
        self.service.retry({'id':first['id']})
        self.wait()
        job = self.service.jobs({})['items'][0]
        self.assertEqual(job['state'],'completed')
        self.assertIn('原上传结果',job['message'])
        self.assertEqual(len(self.drive.files),2)

    def test_confirmed_cancel_cleans_selected_stage(self):
        source = self.root/'rejected.txt'
        source.write_bytes(b'test')
        self.drive.submit = Mock(return_value={'code':'FAIL','batch_id':'failed-batch','batch_state':'FAILED'})
        self.drive.stop = Mock(return_value={'code':'OK','batch_state':'STOPPED'})
        self.service.start_upload({'id':self.select(source)['id']})
        self.wait()
        first = self.service.jobs({})['items'][0]
        target = self.service.work / first['id'] / (first['id'] + '.txt')
        self.assertTrue(target.is_file())
        self.assertTrue(first['cancellable'])
        self.service.cancel_jobs({'id':first['id']})
        self.wait()
        self.assertEqual(self.service.jobs({})['items'][0]['state'],'cancelled')
        self.assertFalse(target.exists())
        self.assertTrue(source.exists())

    def test_same_unconfirmed_digest_blocks_source_rename_and_resumes_original_batch(self):
        path = self.root/'report.dat'
        path.write_bytes(b'contents')
        self.drive.fail_once = True
        self.service.start_upload({'id':self.select(path)['id']})
        self.wait()
        first = self.service.jobs({})['items'][0]
        self.assertEqual(first['state'],'paused')
        alias = self.root/'report copy.dat'
        alias.write_bytes(path.read_bytes())
        self.service.start_upload({'id':self.select(alias)['id']})
        self.wait()
        blocked = self.service.jobs({})['items'][0]
        self.assertEqual(blocked['state'],'paused')
        self.assertEqual(len(self.drive.files),1)
        path.unlink()
        self.service.retry({'id':first['id']})
        self.wait()
        self.assertEqual(self.service.jobs({})['items'][0]['state'],'completed')
        self.assertEqual(len(self.drive.files),2)
