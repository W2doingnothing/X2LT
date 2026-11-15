import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from local_video.libraries import LibraryManager
from local_video.transfer import Paused
from local_video.transfer import Downloader


class LibrariesTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.a=self.root/'a';self.b=self.root/'b'
        self.a.mkdir();self.b.mkdir()
        self.manager=LibraryManager(self.root/'libraries.json')

    def test_persistent_default_overlap_and_removal_keeps_files(self):
        a=self.manager.add('主库',self.a,1000)
        b=self.manager.add('备用',self.b,0)
        with self.assertRaises(ValueError):self.manager.add('重复',self.a)
        child=self.a/'nested';child.mkdir()
        with self.assertRaises(ValueError):self.manager.add('嵌套',child)
        self.manager.set_default(b['id'])
        again=LibraryManager(self.root/'libraries.json')
        self.assertEqual(again.default_id,b['id'])
        (self.a/'keep.txt').write_text('keep')
        again.remove(a['id'])
        self.assertTrue((self.a/'keep.txt').exists())

    def test_capacity_and_atomic_concurrent_reservations(self):
        a=self.manager.add('容量',self.a,1000)
        (self.a/'media').write_bytes(b'x'*100)
        row=self.manager.snapshot(refresh=True)[0]
        self.assertEqual(row['used_bytes'],100)
        self.assertEqual(row['quota_remaining'],900)
        first=self.manager.lease(self.a/'file.mp4');second=self.manager.lease(self.a/'other.mp4')
        with first:
            first.reserve(600)
            with second:
                with self.assertRaises(Paused):second.reserve(400)
        with self.manager.lease(self.a/'other.mp4') as lease:lease.reserve(800)

    def test_offline_and_unknown_library_fail_closed(self):
        a=self.manager.add('主库',self.a,100)
        self.a.rmdir()
        self.assertFalse(self.manager.snapshot(refresh=True)[0]['online'])
        reopened=LibraryManager(self.root/'libraries.json')
        self.assertFalse(reopened.snapshot(refresh=True)[0]['online'])
        with self.assertRaises(Paused):
            with self.manager.lease(self.a/'file') as lease:lease.reserve(1)
        with self.assertRaises(Paused):
            with self.manager.lease(self.root/'outside/file') as lease:lease.reserve(1)

    def test_shared_volume_space_not_double_allocated(self):
        self.manager.add('A',self.a,0);self.manager.add('B',self.b,0)
        fake=type('Disk',(),{'total':1000,'free':700})()
        with patch('local_video.libraries.shutil.disk_usage',return_value=fake):
            with self.manager.lease(self.a/'file') as first:
                first.reserve(400)
                self.assertEqual(self.manager.snapshot(refresh=True)[1]['available_bytes'],300)
                with self.manager.lease(self.b/'file') as second:
                    with self.assertRaises(Paused):second.reserve(400)

    def test_reject_invalid_quota_and_alias(self):
        with self.assertRaises(ValueError):self.manager.add('A',self.a,-1)
        with self.assertRaises(ValueError):self.manager.add('',self.a,0)
        with patch('local_video.libraries.is_alias',return_value=True):
            with self.assertRaises(ValueError):self.manager.add('Alias',self.a,0)

    def test_incomplete_relocation_reserves_old_path_until_journal_cleared(self):
        item=self.manager.add('原库',self.b)
        self.manager.items[0]['previous_paths']=[str(self.a)]
        with self.assertRaises(ValueError):self.manager.add('新独立库',self.a)
        self.manager.items[0]['previous_paths']=[]
        self.manager.add('新独立库',self.a)

    def test_reserved_path_compares_canonical_windows_short_name(self):
        self.manager.add('原库',self.b)
        alias=self.root/'SHORT-NAME'
        self.manager.items[0]['previous_paths']=[str(alias)]
        original=Path.resolve
        def resolve(path,*args,**kwargs):
            return original(self.a) if path==alias else original(path,*args,**kwargs)
        with patch.object(Path,'resolve',resolve):
            with self.assertRaises(ValueError):self.manager.add('新库',self.a)

    def test_downloader_pauses_before_creating_parts_when_quota_too_small(self):
        self.manager.add('小库',self.a,1000)
        downloader=Downloader(storage_guard=self.manager)
        with patch.object(downloader,'_probe',return_value={'total':800,'ranged':False,'validator':'','validator_header':''}):
            with self.assertRaises(Paused):downloader.download('https://video.twimg.com/a.mp4',self.a/'video.mp4')
        self.assertFalse((self.a/'video.mp4.parts').exists())
        self.assertEqual(self.manager.leases,{})

    def test_runner_reports_quota_pause_reason_and_retains_record(self):
        from local_video.store import Store
        from local_video.runner import Runner
        from local_video.discovery import Video
        self.manager.add('小库',self.a,1000)
        store=Store(self.root/'queue.db');self.addCleanup(store.close)
        job=store.enqueue(Video('123','1','fixture','https://video.twimg.com/a.mp4','https://x.com/fixture/status/123'),self.a,'best')
        events=[];runner=Runner(store,{'output':str(self.a),'_storage_guard':self.manager},{},events.append)
        with patch('local_video.transfer.Downloader._probe',return_value={'total':800,'ranged':False,'validator':'','validator_header':''}):runner.run()
        row=store.get_many([job])[0]
        self.assertEqual(row['state'],'paused')
        self.assertIn('配额',row['error'])
        self.assertTrue(any('配额' in e.get('text','') for e in events))


if __name__=='__main__':unittest.main()
