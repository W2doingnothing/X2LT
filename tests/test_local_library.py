import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

from PIL import Image

from local_video.desktop_service import DesktopService
from local_video.transfer import Paused


class LocalLibraryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.service = self.new_service()
        self.addCleanup(lambda: self.service._close())
        (self.root/'library').mkdir()
        (self.root/'other').mkdir()
        self.library = self.service._libraries.add('图片和漫画', self.root/'library')['id']
        self.other = self.service._libraries.add('副库', self.root/'other')['id']

    def new_service(self):
        return DesktopService(self.root/'data', self.root/'home')

    def call(self, action, **payload):
        response = self.service.call(action,payload)
        self.assertTrue(response['ok'],response)
        return response['data']

    def wait(self, job, stage):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status = self.call('local_import_status',id=job)
            if status['stage'] not in ('importing','scanning'):
                self.assertEqual(status['stage'],stage,status)
                return status
            time.sleep(.02)
        self.fail('本地入库任务未完成')

    def preview(self, paths, kind='media', storage='reference', library=None):
        self.service._native.choose_local_import = Mock(return_value=tuple(paths))
        selection = self.call('local_pick',kind=kind,selection='files')['id']
        job = self.call('local_scan',selection=selection,library_id=library or self.library,
                        kind=kind,storage=storage)['id']
        return self.wait(job,'preview')

    def entries(self, library=None, **filters):
        return self.call('local_library',library_id=library or self.library,**filters)['items']

    def start(self, preview):
        self.call('local_import_start',id=preview['id'])
        self.wait(preview['id'],'completed')

    def test_reference_dedup_relocation_tags_and_cross_library_links(self):
        source = self.root/'picture.png'
        Image.new('RGB',(8,12)).save(source)
        preview = self.preview([source])
        self.assertEqual(preview['copy_bytes'],0)
        self.start(preview)
        item = self.entries()[0]
        key = item['id']
        self.call('local_media_tags',id=key,tags=['图片'])
        self.call('local_media_link',id=key,library_id=self.other,linked=True)
        self.assertEqual([i['id'] for i in self.entries(self.other,tags=['图片'])],[key])
        snapshot = self.call('snapshot',library_id=self.other,collection=True)
        self.assertEqual(snapshot['tag_options'],[{'name':'图片','count':1}])
        self.call('tag_rename',old='图片',new='封面')
        renamed = source.with_name('renamed.png')
        source.rename(renamed)
        self.assertEqual(self.entries()[0]['locations'][0]['state'],'missing')
        self.service._native.choose_local_import.return_value = (renamed,)
        self.call('local_media_relocate',id=key,location_id=item['locations'][0]['id'])
        self.assertTrue(self.call('local_media_preview',id=key).startswith('data:image/jpeg;base64,'))
        preview = self.preview([renamed])
        self.assertEqual(preview['duplicate'],1)
        self.start(preview)
        self.assertEqual([i['id'] for i in self.entries(tags=['封面'])],[key])
        self.call('local_media_remove',id=key,library_id=self.library)
        self.assertFalse(self.entries())
        self.assertTrue(renamed.is_file())
        self.assertEqual(self.entries(self.other)[0]['id'],key)

    def test_verified_copy_cancellation_and_retry(self):
        source = self.root/'copy.png'
        Image.new('RGB',(8,8)).save(source)
        preview = self.preview([source],storage='copy')
        self.assertEqual(preview['copy_bytes'],source.stat().st_size)
        invoked = threading.Event()

        def interrupted_copy(_source, _kind, _pages, staging, cancel):
            staging.write_bytes(b'incomplete')
            invoked.set()
            cancel.wait(5)
            raise Paused('已取消')

        with patch('local_video.services.local_library.copy_into',side_effect=interrupted_copy):
            self.call('local_import_start',id=preview['id'])
            self.assertTrue(invoked.wait(5))
            self.call('local_import_cancel',id=preview['id'])
            self.wait(preview['id'],'cancelled')
        library_root = self.root/'library'
        self.assertFalse(list(library_root.rglob('.incoming-*')))
        self.assertFalse(self.entries())
        self.call('local_import_retry',id=preview['id'])
        self.wait(preview['id'],'completed')
        copies = list(library_root.rglob('*.png'))
        self.assertEqual(len(copies),1)
        self.assertEqual(copies[0].read_bytes(),source.read_bytes())
        preview = self.preview([source],storage='copy')
        self.assertEqual(preview['copy_bytes'],0)
        self.start(preview)
        self.assertEqual(len(self.entries()),1)
        item = self.entries()[0]
        key, location = item['id'], item['locations'][0]['id']
        self.call('local_media_link',id=key,library_id=self.other,linked=True)
        blocked = self.service.call('local_media_remove',dict(id=key,library_id=self.library,
                                    location_id=location,recycle=True))
        self.assertFalse(blocked['ok'])
        self.call('library_remove',id=self.other)
        with patch('local_video.services.local_library.recycle_file',side_effect=lambda path: path.unlink()) as recycle:
            self.call('local_media_remove',id=key,library_id=self.library,location_id=location,recycle=True)
            recycle.assert_called_once_with(copies[0])
        self.assertTrue(source.is_file())
        self.assertFalse(copies[0].is_file())

    def test_cbz_order_cover_and_reading_position_survive_restart(self):
        comic = self.root/'漫画.cbz'
        broken = self.root/'bad.cbz'
        broken.write_bytes(b'invalid')
        buffer = io.BytesIO()
        Image.new('RGB',(8,12)).save(buffer,'PNG')
        with ZipFile(comic,'w') as archive:
            for name in ('10.png','2.png','1.png'):
                archive.writestr(name,buffer.getvalue())
        preview = self.preview([broken,comic],kind='comic',storage='copy')
        self.assertEqual(preview['failed'],1)
        self.assertEqual(len(preview['items']),1)
        self.start(preview)
        key = self.entries()[0]['id']
        state = self.call('local_comic_get',id=key)
        self.assertEqual(state['title'],'漫画')
        self.assertEqual([p['name'] for p in state['pages']],['1.png','2.png','10.png'])
        order = [p['id'] for p in reversed(state['pages'])]
        self.call('local_comic_update',id=key,order=order,cover=order[1],spread=True,rtl=True)
        self.call('local_comic_update',id=key,page=1)
        self.assertTrue(self.service._close())
        self.service = self.new_service()
        state = self.call('local_comic_get',id=key)
        self.assertEqual([p['id'] for p in state['pages']],order)
        self.assertEqual((state['page'],state['spread'],state['rtl'],state['cover']),(1,True,True,order[1]))
        self.assertTrue(self.call('local_comic_page',id=key,page_id=order[1]).startswith('data:image/jpeg;base64,'))
