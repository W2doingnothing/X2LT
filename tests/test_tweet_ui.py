import gc
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from local_video.app import App
from tests.test_tweet_cards import video


class TweetUITests(unittest.TestCase):
    def test_new_tweet_appears_first_without_replacing_existing_card(self):
        self.seed('123');self.app._refresh();old=self.app.shell.task_cards['123']
        job=self.seed('456');self.app._refresh([job])
        shell=self.app.shell
        self.assertEqual(list(shell.task_cards),['456','123'])
        self.assertIs(shell.task_cards['123'],old)
        self.assertIs(shell.task_area.body.pack_slaves()[0],shell.task_cards['456'].card)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=tk.Tk();self.root.withdraw()
        self.app=App(self.root,home=Path(self.temp.name),data_dir=Path(self.temp.name)/'private')

    def tearDown(self):
        self.app.close();self.temp.cleanup();del self.app,self.root;gc.collect()

    def seed(self,tweet='123',media='1',quality='best'):
        return self.app.store.enqueue(video(tweet,media),self.app.output.get(),quality)

    def test_one_card_three_previews_carousel_and_profile(self):
        jobs=[self.seed(media=str(i)) for i in range(1,5)]
        self.app.store.set_state(jobs[0],'completed',bytes=4,sha256='a'*64)
        self.app._refresh()
        self.assertEqual(list(self.app.shell.task_cards),['123'])
        card=self.app.shell.task_cards['123']
        self.assertEqual([t.row['media_id'] for t in card.tiles],['1','2','3'])
        self.assertEqual(card.title['text'],'推文标题')
        self.assertEqual(card.profile['text'],'@alice')
        with patch('webbrowser.open') as opened:card.open_profile()
        opened.assert_called_once_with('https://x.com/alice')
        old_token=self.app.preview.labels[card.tiles[0]]['token']
        card.shift(1)
        self.assertEqual([t.row['media_id'] for t in card.tiles],['2','3','4'])
        self.assertNotIn(old_token,self.app.preview.tokens)

    def test_ring_progress_is_independent_and_unknown_length_is_honest(self):
        a,b=self.seed(media='1'),self.seed(media='2')
        self.app.store.set_state(a,'downloading');self.app.store.set_state(b,'paused')
        self.app._refresh();card=self.app.shell.task_cards['123']
        self.app.shell.progress(a,50,100,1024)
        self.assertEqual(card.tiles[0].itemcget(card.tiles[0].progress_text,'text'),'50%')
        self.assertEqual(card.tiles[1].itemcget(card.tiles[1].progress_text,'text'),'已暂停')
        self.app.shell.progress(a,50,None,1024)
        self.assertNotIn('%',card.tiles[0].itemcget(card.tiles[0].progress_text,'text'))

    def test_selection_across_pages_batch_ignore_and_unignore_no_auto_start(self):
        for i in range(101):self.seed(tweet=str(1000+i))
        self.app._refresh();shell=self.app.shell
        first=next(iter(shell.task_cards));shell.toggle_tweet(first,True)
        shell.turn_page(1);second=next(iter(shell.task_cards));shell.toggle_tweet(second,True)
        self.assertEqual(shell.selected_tweets,{first,second})
        shell.batch_ignore()
        self.assertTrue(all(self.app.store.is_ignored(i) for i in (first,second)))
        shell.filter.set('已忽略');shell.change_filter()
        self.assertEqual(set(shell.task_cards),{first,second})
        shell.select_page()
        with patch.object(self.app,'start',side_effect=AssertionError('automatic download')):shell.batch_ignore()
        self.assertFalse(self.app.store.is_ignored(first))

    def test_busy_ignore_and_force_guard(self):
        job=self.seed();self.app._refresh();card=self.app.shell.task_cards['123']
        self.app._busy(True)
        self.assertEqual(str(card.ignore_button['state']),'normal')
        self.app.ignore_tweets(['123'])
        self.app._busy(False);self.app.shell.select(job)
        with patch('local_video.app.messagebox.showerror') as error:
            self.app.start('force')
        self.assertTrue(error.called)
        self.assertIsNone(self.app.worker)

    def test_detail_navigates_media_versions_and_force_selects_current(self):
        a=self.seed(media='1');b=self.seed(media='2');c=self.seed(media='2',quality='480p')
        self.app._refresh();self.app.shell.show_detail(a)
        self.assertEqual(len(self.app.preview.detail_rows),3)
        self.app.preview.choose_version(2)
        self.assertEqual(self.app.preview.detail_job,c)
        with patch.object(self.app,'start') as start:
            self.app.force_media(c)
        start.assert_called_once_with('force')
        self.assertEqual(self.app.tree.selection(),(str(c),))
