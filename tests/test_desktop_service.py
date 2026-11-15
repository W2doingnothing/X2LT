import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from local_video.desktop_service import DesktopService
from local_video.discovery import Video
from local_video.archive import save_receipt,valid_receipt,original_path


class DesktopTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.home=self.root/'home';self.home.mkdir()
        self.service=DesktopService(self.root/'private',self.home)
        self.addCleanup(self.service._close)

    def call(self,action,payload=None):
        result=self.service.call(action,payload or {})
        self.assertTrue(result['ok'],result)
        return result.get('data')

    def test_snapshot_persistent_theme_and_no_secrets(self):
        self.call('settings',{'theme':'dark','accent':'#ff0066'})
        self.assertEqual(self.call('snapshot')['settings']['accent'],'#ff0066')
        saved=json.loads((self.root/'private/settings.json').read_text())
        self.assertEqual(saved['theme'],'dark')
        self.assertNotIn('proxy',saved)
        self.assertFalse(self.service.call('settings',{'accent':'url(javascript:alert(1))'})['ok'])
        self.assertFalse(self.service.call('arbitrary_action',{})['ok'])

    def test_legacy_quality_label_does_not_break_account_settings_save(self):
        self.service._settings['quality_label']='720p'
        settings=self.call('snapshot')['settings']
        self.assertNotIn('quality_label',settings)
        self.call('settings',settings)

    def test_group_snapshot_excludes_signed_urls_and_credentials(self):
        output=self.service._libraries.get()['path']
        for i in range(4):self.service._store.enqueue(Video('123',str(i),'author','https://video.twimg.com/a.mp4?secret=signed','https://x.com/author/status/123',tweet_text='text'),output,'best')
        data=self.call('snapshot')
        self.assertEqual(len(data['tasks']),1);self.assertEqual(len(data['tasks'][0]['media']),4)
        self.assertNotIn('signed',json.dumps(data))
        self.call('ignore',{'ids':['123'],'ignored':True})
        self.assertEqual(len(self.call('snapshot')['tasks']),0)
        self.assertEqual(len(self.call('snapshot',{'filter':'ignored'})['tasks']),1)

    def test_folder_must_come_from_native_picker(self):
        folder=self.root/'another';folder.mkdir()
        self.assertFalse(self.service.call('library_add',{'name':'新库','path':str(folder),'quota_bytes':0})['ok'])
        self.service._approved_paths.add(str(folder.resolve()))
        self.call('library_add',{'name':'新库','path':str(folder),'quota_bytes':100000000})
        self.assertEqual(len(self.call('snapshot')['libraries']),2)

    def test_active_output_snapshot_cannot_change_with_default(self):
        first=self.service._libraries.get()
        second_path=self.root/'second';second_path.mkdir()
        second=self.service._libraries.add('第二库',second_path)
        settings=self.service._run_settings(first['id'])
        self.call('library_default',{'id':second['id']})
        self.assertEqual(settings['output'],first['path'])
        self.assertEqual(self.service._run_settings(None)['output'],second['path'])

    def test_force_ignored_and_invalid_profiles_blocked(self):
        output=self.service._libraries.get()['path']
        jid=self.service._store.enqueue(Video('123','1','author','https://video.twimg.com/a.mp4','https://x.com/author/status/123'),output,'best')
        self.call('ignore',{'ids':['123'],'ignored':True})
        self.assertFalse(self.service.call('start',{'mode':'force','job_id':jid})['ok'])
        self.assertFalse(self.service.call('open_profile',{'handle':'../bad'})['ok'])

    def test_relocate_moved_library_preserves_queue_and_receipt(self):
        lib=self.service._libraries.get();old=Path(lib['path'])
        job=self.service._store.enqueue(Video('123','1','author','https://video.twimg.com/a.mp4','https://x.com/author/status/123'),old,'best')
        self.service._store.set_state(job,'completed',bytes=4,sha256='a'*64,verification='length+sha256')
        row=self.service._store.get_many([job])[0];save_receipt(row)
        moved=self.root/'moved';old.rename(moved);self.service._approved_paths.add(str(moved.resolve()))
        self.call('library_update',{'id':lib['id'],'path':str(moved)})
        updated=self.service._store.get_many([job])[0]
        self.assertEqual(updated['output'],str(moved.resolve()))
        self.assertIsNotNone(valid_receipt(updated))
        self.assertEqual(updated['state'],'completed')
        old.mkdir()
        self.service._libraries.add('新独立库',old)
        other=self.service._store.enqueue(Video('456','1','author','https://video.twimg.com/b.mp4','https://x.com/author/status/456'),old,'best')
        self.service._repair_library_paths()
        self.assertEqual(self.service._store.get_many([other])[0]['output'],str(old))

    def test_bridge_rejects_external_navigation(self):
        class Window:
            real_url='http://127.0.0.1:12345/index.html?desktop=1'
            def get_current_url(self):return 'https://evil.test/index.html'
        self.service._window=Window()
        self.assertFalse(self.service.call('snapshot',{})['ok'])

    def test_search_keeps_sibling_states_when_filtering_completed_tweets(self):
        output=self.service._libraries.get()['path']
        first=self.service._store.enqueue(Video('123','1','fixture','https://video.twimg.com/a.mp4','https://x.com/fixture/status/123',tweet_text='unique match'),output,'best')
        self.service._store.set_state(first,'completed',bytes=4,sha256='a'*64)
        self.service._store.enqueue(Video('123','2','fixture','https://video.twimg.com/b.mp4','https://x.com/fixture/status/123',tweet_text='other metadata'),output,'best')
        self.assertEqual(self.call('snapshot',{'query':'unique match','filter':'completed'})['total'],0)
        self.assertEqual(len(self.call('snapshot',{'query':'unique match'})['tasks'][0]['media']),2)


if __name__=='__main__':unittest.main()
