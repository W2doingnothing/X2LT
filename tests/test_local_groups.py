from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from local_video.local_collection import LocalCollection
from local_video.local_groups import LocalGroups
from local_video.store import Store


class LocalGroupTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        store = Store(self.root/'queue.sqlite')
        self.addCleanup(store.close)
        self.collection = LocalCollection(store)
        self.groups = LocalGroups(self.collection)

    def add(self, name, kind='image'):
        path = self.root/(name+'.png')
        data = name.encode()
        path.write_bytes(data)
        return self.collection.add(dict(kind=kind,title=name,extension='png',bytes=len(data),pages=[]),
                                   sha256(data).hexdigest(),path,self.root,'library',False)

    def page(self, query='', tags=(), library='library'):
        return self.groups.page(library,query,tags,False,0,50)

    def change(self, operation, **payload):
        return self.groups.change('library',dict(operation=operation,**payload))

    def test_members_and_title_share_search_results_without_duplicate_cards(self):
        a,b,c = (self.add(name) for name in ('猫','狗','兔'))
        self.collection.set_tags(a,['野外'])
        self.collection.set_link(a,'other',True)
        self.change('merge',ids=[b,a],target_id=a)
        group = next(row['group'] for row in self.page()['items'] if 'group' in row)
        self.change('edit',group_id=group['id'],title='动物',cover_id=b,order=[b,a])
        results = self.page('动物',('野外',))
        self.assertEqual(results['total'],1)
        card = results['items'][0]
        self.assertEqual((card['title'],card['id']),('动物',b))
        self.assertEqual([member['id'] for member in card['group']['items']],[b,a])
        self.assertEqual(self.page('狗')['total'],1)
        self.assertEqual(self.page(library='other')['items'][0]['id'],a)
        self.assertEqual(self.page()['total'],2)
        self.assertEqual(self.groups.page('library','',[],False,1,1)['total'],2)
        keys = [row['id'] for row in self.page()['items']]
        self.assertIn(c,keys)

    def test_dissolve_undo_and_unlink_keep_original_files(self):
        a,b = self.add('一'),self.add('二')
        self.change('merge',ids=[a,b])
        key = self.page()['items'][0]['group']['id']
        undo = self.change('dissolve',group_id=key)['undo']
        self.assertEqual(self.page()['total'],2)
        self.change('undo',undo=undo)
        self.assertEqual(self.page()['items'][0]['group']['id'],key)
        self.collection.set_link(a,'library',False)
        self.collection.set_link(a,'library',True)
        self.assertEqual(self.page()['total'],2)
        self.assertTrue((self.root/'一.png').exists())

    def test_undo_rejects_conflicting_edit_and_unrelated_library_ids(self):
        a,b,c = (self.add(name) for name in ('a','b','c'))
        undo = self.change('merge',ids=[a,b])['undo']
        key = next(row['group']['id'] for row in self.page()['items'] if 'group' in row)
        self.change('merge',group_id=key,ids=[c])
        with self.assertRaises(ValueError):
            self.change('undo',undo=undo)
        self.collection.set_link(c,'library',False)
        with self.assertRaises(ValueError):
            self.change('merge',group_id=key,ids=[c])
        self.assertEqual(len(self.page()['items'][0]['group']['items']),2)

    def test_moving_members_between_groups_undo_restores_both_orders(self):
        a,b,c,d = (self.add(name) for name in ('a','b','c','d'))
        self.change('merge',ids=[a,b])
        self.change('merge',ids=[d,c])
        before = {row['group']['id']:[item['id'] for item in row['group']['items']]
                  for row in self.page()['items']}
        target = next(key for key,members in before.items() if members == [d,c])
        undo = self.change('merge',group_id=target,ids=[b])['undo']
        self.assertEqual(self.groups.members(target),[d,c,b])
        self.assertEqual(self.page()['total'],2)
        self.change('undo',undo=undo)
        self.assertEqual({key:self.groups.members(key) for key in before},before)

    def test_card_text_is_separate_from_filename_and_survives_group_changes(self):
        a,b = self.add('原始文件'),self.add('第二项')
        with self.assertRaises(ValueError):
            self.collection.edit_text('other',a,'无权编辑','')
        self.collection.edit_text('library',a,'自定义标题','正文第一行\n正文第二行')
        self.assertEqual(self.collection.entry(a)['title'],'原始文件')
        self.assertEqual(self.page('正文第二行')['items'][0]['title'],'自定义标题')
        self.assertEqual(self.page('原始文件')['total'],1)
        self.change('merge',ids=[a,b])
        group = self.page()['items'][0]['group']
        self.change('edit',group_id=group['id'],body='集合正文')
        result = self.page('集合正文')['items'][0]
        self.assertEqual(result['body'],'集合正文')
        self.assertEqual(result['group']['items'][0]['body'],'正文第一行\n正文第二行')
        undo = self.change('dissolve',group_id=group['id'])['undo']
        self.assertEqual(self.page('自定义标题')['items'][0]['body'],'正文第一行\n正文第二行')
        self.change('undo',undo=undo)
        self.assertEqual(self.page('集合正文')['items'][0]['group']['body'],'集合正文')
        self.collection.edit_text('library',a,'','')
        self.assertEqual(self.page('原始文件')['items'][0]['group']['items'][0]['title'],'原始文件')

    def test_existing_group_and_entries_gain_empty_text_fields(self):
        first, second = self.add('first'), self.add('second')
        self.change('merge',ids=[first,second])
        db = self.collection.db
        with db:
            db.execute('ALTER TABLE local_entries DROP COLUMN display_title')
            db.execute('ALTER TABLE local_entries DROP COLUMN body')
            db.execute('ALTER TABLE local_groups DROP COLUMN body')

        self.collection = LocalCollection(self.collection.store)
        self.groups = LocalGroups(self.collection)
        group = self.page()['items'][0]['group']
        self.assertEqual(group['body'],'')
        self.assertEqual([(item['title'],item['body']) for item in group['items']],[('first',''),('second','')])
