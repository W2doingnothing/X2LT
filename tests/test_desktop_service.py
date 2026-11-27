from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from local_video.desktop_service import DesktopService
from local_video.discovery import Video
from local_video.archive import save_receipt,valid_receipt,original_path


class DesktopTest(unittest.TestCase):
    def test_removal_settings_and_scoped_plan_contract(self):
        lib = self.service._libraries.get()
        self.service._store.enqueue(Video('123','1','alice','https://video.twimg.com/a.mp4','https://x.com/alice/status/123'),lib['path'],'best')
        self.assertTrue(self.call('snapshot')['settings']['confirm_removal'])
        self.call('settings', {'confirm_removal':False})
        self.assertFalse(self.call('snapshot')['settings']['confirm_removal'])
        self.assertFalse(self.service.call('settings', {'confirm_removal':'false'})['ok'])
        plan = self.call('removal_plan', {'ids':['123'],'library_id':lib['id']})
        self.assertEqual(plan['content_count'],1)
        self.assertNotIn('rows',plan)
        self.assertFalse(self.service.call('removal_plan', {'ids':['123'],'library_id':'missing'})['ok'])
        self.call('ignore',{'ids':['123']})
        filters = self.call('filters')
        self.assertEqual(filters['items'][0]['author'],'alice')
        self.call('filters_clear',{'keys':[filters['items'][0]['key']]})
        self.assertEqual(self.call('filters')['total'],0)

    def test_collection_snapshot_authors_saved_media_and_library_validation(self):
        library=self.service._libraries.get()
        job=self.service._store.enqueue(Video('123','1','alice','https://video.twimg.com/a.mp4?secret=signed','https://x.com/alice/status/123',author_id='42',display_name='Alice'),library['path'],'best')
        self.service._store.set_state(job,'completed')
        self.service._store.enqueue(Video('123','2','alice','https://video.twimg.com/b.mp4','https://x.com/alice/status/123'),library['path'],'best')
        self.call('ignore',{'ids':['123'],'ignored':True})
        params={'library_id':library['id'],'collection':True}
        authors=self.call('snapshot',{**params,'view':'authors'})
        self.assertEqual(authors['authors'][0]['key'],'id:42')
        self.assertEqual(authors['authors'][0]['media_count'],1)
        self.assertEqual(authors['collection_author_count'],1)
        self.assertEqual(authors['tasks'],[])
        page=self.call('snapshot',{**params,'author_key':'id:42'})
        self.assertEqual(len(page['tasks'][0]['media']),1)
        self.assertTrue(page['tasks'][0]['ignored'])
        self.assertNotIn('signed',json.dumps(authors))
        self.assertNotIn('avatar_url',json.dumps(authors))
        self.assertFalse(self.service.call('snapshot',{'collection':True})['ok'])
        self.assertFalse(self.service.call('snapshot',{**params,'library_id':'nonexistent'})['ok'])
        old=self.call('author_preferences',{'library_id':library['id'],'keys':['id:42'],'starred':True,'in_other':True})
        self.assertEqual(old,[{'key':'id:42','starred':False,'in_other':False}])
        grouped=self.call('snapshot',{**params,'view':'authors'})
        self.assertTrue(grouped['authors'][0]['is_group'])
        other=self.call('snapshot',{**params,'view':'authors','author_group':'other'})
        self.assertTrue(other['authors'][0]['starred'])
        self.assertFalse(self.service.call('author_preferences',{'library_id':library['id'],'keys':['id:42'],'starred':'true'})['ok'])
        self.assertFalse(self.service.call('author_preferences',{'library_id':'nonexistent','keys':['id:42'],'in_other':False})['ok'])
        self.call('author_preferences',{'library_id':library['id'],'keys':['id:42'],'restore':old})
        self.assertFalse(self.call('snapshot',{**params,'view':'authors'})['authors'][0]['is_group'])

    def test_resources_archived_authors_and_configured_scope(self):
        library=self.service._libraries.get()
        job=self.service._store.enqueue(Video('123','1','alice','https://video.twimg.com/a.mp4?secret=signed','https://x.com/alice/status/123',author_id='42'),library['path'],'best')
        self.service._store.set_state(job,'completed')
        overview=self.call('resource_overview')
        self.assertEqual(overview['video_count'],1)
        self.assertEqual(overview['total'],1)
        self.assertEqual(overview['resources'][0]['locations'][0]['id'],job)
        self.assertNotIn('signed',json.dumps(overview))
        self.call('author_preferences',{'library_id':library['id'],'keys':['id:42'],'starred':True,'archived':True})
        args={'library_id':library['id'],'collection':True,'view':'authors'}
        self.assertEqual(self.call('snapshot',{**args,'author_group':'active'})['authors'],[])
        archived=self.call('snapshot',{**args,'author_group':'archive'})['authors']
        self.assertEqual([a['key'] for a in archived],['id:42'])
        self.assertTrue(archived[0]['starred'])
        self.assertFalse(archived[0]['is_group'])
        self.assertEqual(self.call('resource_overview')['video_count'],1)
        self.assertEqual(self.call('snapshot')['libraries'][0]['video_count'],1)
        self.service._libraries.items=[]
        self.assertEqual(self.call('resources')['total'],0)

    def test_sources_count_unique_saved_media_and_filter_before_browsing(self):
        library=self.service._libraries.get()
        for quality in ['best','720p']:
            job=self.service._store.enqueue(Video('123','1','alice','https://video.twimg.com/a.mp4?secret=signed','https://x.com/alice/status/123'),library['path'],quality)
            self.service._store.set_state(job,'completed')
        self.service._store.enqueue(Video('124','2','bob','https://video.twimg.com/b.mp4','https://x.com/bob/status/124'),library['path'],'best')
        self.call('ignore',{'ids':['123'],'ignored':True})
        args={'library_id':library['id'],'collection':True}
        page=self.call('snapshot',{**args,'view':'sources'})
        self.assertEqual(page['sources'],[{'key':'x','label':'X','message_count':1,'media_count':1}])
        self.assertEqual(page['total'],1)
        self.assertEqual(self.call('snapshot',{**args,'query':'123_1.mp4'})['total'],1)
        self.assertNotIn('signed',json.dumps(page))
        self.assertEqual(len(self.call('snapshot',{**args,'source_key':'x'})['tasks']),1)
        self.assertEqual(self.call('snapshot',{**args,'source_key':'local'})['total'],0)
        self.assertEqual(self.call('snapshot',{**args,'view':'sources','query':'other'})['sources'],[])
        self.call('tags_set',{'id':'123','tags':['旅行']})
        self.assertEqual(self.call('snapshot',{**args,'view':'sources','tags':['旅行']})['total'],1)
        self.assertEqual(self.call('snapshot',{**args,'view':'sources','untagged':True})['total'],0)

    def test_library_cards_interleaves_archived_content_and_filters_before_paging(self):
        library = self.service._libraries.get()
        folder = Path(library['path'])
        folder.mkdir(parents=True, exist_ok=True)
        store = self.service._store
        collection = self.service._local.collection
        archive = self.service._telegram._archive

        local_path = folder/'local.png'
        local_path.write_bytes(b'local')
        with patch('time.time', return_value=100):
            local_id = collection.add(dict(kind='image', title='旅行本地', extension='png', bytes=5, pages=[]),
                                      sha256(b'local').hexdigest(), local_path, folder, library['id'], True)
        another_path = folder/'extra.png'
        another_path.write_bytes(b'extra')
        with patch('time.time', return_value=250):
            another_id = collection.add(dict(kind='image', title='旅行图片', extension='png', bytes=5, pages=[]),
                                        sha256(b'extra').hexdigest(), another_path, folder, library['id'], True)

        def save_x(identifier, saved_at, output):
            video = Video(identifier,'1','alice','https://video.twimg.com/x.mp4?secret=signed',
                          f'https://x.com/alice/status/{identifier}',tweet_text='旅行归档')
            with patch('time.time', return_value=saved_at):
                job = store.enqueue(video,output,'best')
                store.set_state(job,'completed')
            return video

        old_x = save_x('123', 200, folder)
        save_x('124', 400, folder)
        save_x('125', 500, self.root/'another-library')
        store.enqueue(Video('126','1','bob','https://video.twimg.com/p.mp4','https://x.com/bob/status/126'), folder,'best')
        with patch('time.time', return_value=600):
            store.enqueue(old_x,folder,'best')

        for index in range(2):
            telegram_path = folder/f'telegram-{index}.png'
            data = [b'local', b'other'][index]
            telegram_path.write_bytes(data)
            telegram_job = dict(library_id=library['id'],extension='.png',peer_id='-100001',
                                message_id=str(9+index),peer_title='频道',group_id='42',title='旅行频道',
                                body='旅行归档',media_id='1',kind='image',width=0,height=0,
                                size=len(data),url='https://t.me/channel/9',sent_at=10)
            with patch('time.time', return_value=300):
                archive.register(telegram_job,telegram_path,sha256(data).hexdigest())

        args = {'library_id':library['id'],'limit':2}
        self.assertTrue(self.call('bootstrap')['library_cards'])
        summary = self.call('snapshot',{**args,'collection':True,'summary_only':True})
        self.assertEqual(summary['tasks'], [])
        self.assertEqual(summary['collection_author_count'], 1)
        first = self.call('library_cards',args)
        self.assertEqual((first['total'],first['offset']), (4,0))
        self.assertEqual([item['type'] for item in first['items']], ['x','local'])
        self.assertEqual(first['items'][0]['task']['id'], '124')
        telegram = first['items'][1]['entry']
        self.assertEqual(len(telegram['telegram_items']), 2)
        self.assertEqual(telegram['telegram']['channel'], '频道')
        self.assertNotIn('signed', json.dumps(first))
        last = self.call('library_cards',{**args,'offset':99})
        self.assertEqual((last['total'],last['offset']), (4,2))
        self.assertEqual(last['items'][0]['entry']['id'], another_id)
        self.assertEqual(last['items'][1]['task']['id'], '123')

        collection.set_tags(local_id,['收藏'])
        store.catalog.set_tags('123',['收藏'])
        filtered = self.call('library_cards',{**args,'query':'旅行 #收藏','tags':['收藏']})
        self.assertEqual(filtered['total'], 2)
        self.assertEqual([item['type'] for item in filtered['items']], ['local','x'])
        self.assertEqual(len(filtered['items'][0]['entry']['telegram_items']), 2)
        untagged = self.call('library_cards',{**args,'query':'旅行','untagged':True})
        self.assertEqual(untagged['total'], 3)
        self.assertEqual(untagged['items'][0]['task']['id'], '124')
        self.assertFalse(self.service.call('library_cards',{**args,'library_id':'missing'})['ok'])

    def test_privacy_mode_is_boolean_and_persists(self):
        self.assertFalse(self.call('snapshot')['settings']['privacy_mode'])
        self.call('settings',{'privacy_mode':True})
        self.assertTrue(self.call('snapshot')['settings']['privacy_mode'])
        self.assertTrue(json.loads((self.root/'private/settings.json').read_text())['privacy_mode'])
        self.assertFalse(self.service.call('settings',{'privacy_mode':'true'})['ok'])

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

    def test_library_scoped_tags_and_counts_do_not_leak_other_copies(self):
        lib=self.service._libraries.get();other=self.root/'other';other.mkdir();second=self.service._libraries.add('第二库',other)
        video=Video('123','1','fixture','https://video.twimg.com/a.mp4','https://x.com/fixture/status/123',tweet_text='text')
        job=self.service._store.enqueue(video,lib['path'],'best');self.service._store.set_state(job,'completed')
        self.service._store.enqueue(Video('123','2','fixture',video.url,video.source),second['path'],'best')
        self.call('tags_set',{'id':'123','tags':['#旅行','Art']})
        snap=self.call('snapshot',{'library_id':lib['id'],'tags':['旅行'],'filter':'completed'})
        self.assertEqual(snap['total'],1);self.assertEqual(len(snap['tasks'][0]['media']),1)
        self.assertEqual(set(snap['tasks'][0]['tags']),{'旅行','Art'})
        self.assertEqual(snap['libraries'][0]['message_count'],1)
        self.assertEqual(self.call('snapshot',{'library_id':second['id'],'filter':'completed'})['total'],0)
        self.assertFalse(self.service.call('snapshot',{'library_id':'unknown'})['ok'])

    def test_cover_emoji_and_native_image_import(self):
        from PIL import Image
        lib=self.service._libraries.get();self.call('library_cover',{'id':lib['id'],'kind':'emoji','value':'📚'})
        self.assertEqual(self.call('snapshot')['libraries'][0]['cover'],{'kind':'emoji','value':'📚'})
        self.assertFalse(self.service.call('library_cover',{'id':lib['id'],'kind':'image','path':'arbitrary.png'})['ok'])
        source=self.root/'cover.png';Image.new('RGB',(50,30),'blue').save(source)
        class Window:
            def create_file_dialog(self,*args,**kwargs):return [str(source)]
        # Exercise dispatch under the same native-picker path, without bypassing origin checks.
        self.service._window=Window()
        self.service._dispatch('library_cover',{'id':lib['id'],'kind':'image'})
        self.service._window=None;source.unlink()
        self.assertTrue(self.call('cover',{'id':lib['id']}).startswith('data:image/jpeg;base64,'))
        self.call('library_cover',{'id':lib['id'],'kind':'reset'})
        self.assertIsNone(self.call('cover',{'id':lib['id']}))

    def test_operation_survives_queue_overflow_and_ignores_old_run(self):
        run=self.service._operation.begin(reading=True)
        self.service._emit({'type':'run_stats','run_id':run,'discovered_media':7})
        for i in range(2100):self.service._emit({'type':'message','text':'fixture'})
        self.assertEqual(self.call('poll')['operation']['discovered_media'],7)
        fresh=self.service._operation.begin()
        self.service._emit({'type':'finished','run_id':run})
        self.assertEqual(self.call('snapshot')['operation']['run_id'],fresh)

    def test_paging_uses_cached_capacity_even_when_cache_is_old(self):
        from dataclasses import replace
        self.call('library_refresh')
        lib=self.service._libraries.get()
        sample=self.service._libraries.cache[lib['id']]
        self.service._libraries.cache[lib['id']]=replace(sample,measured_at=sample.measured_at-60)
        with patch('local_video.libraries.os.walk',side_effect=AssertionError('paging walked media directories')):
            self.assertTrue(self.service.call('snapshot',{'offset':50})['ok'])
        with patch('local_video.libraries.os.walk',wraps=__import__('os').walk) as walk:
            self.call('library_refresh');self.assertTrue(walk.called)


if __name__=='__main__':unittest.main()
