import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from local_video.app import App
from local_video.discovery import Video


class CardUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.home = Path(self.temp.name)
        self.app = App(self.root, home=self.home, data_dir=self.home / 'private')

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_theme_persists_without_saving_plaintext_or_recreating_queue(self):
        store = self.app.store
        self.app.auth.set('fixture-unsaved-secret')
        self.app.set_theme('dark')
        self.assertEqual(self.app.theme.get(), 'dark')
        self.assertIs(self.app.store, store)
        saved = self.app.settings_file.read_text(encoding='utf8')
        self.assertEqual(json.loads(saved)['theme'], 'dark')
        self.assertNotIn('fixture-unsaved-secret', saved)
        self.app.set_theme('light')
        self.assertEqual(self.app.shell.palette['bg'], '#eef0f5')

    def test_navigation_cards_selection_and_true_credential_status(self):
        job = self.app.store.enqueue(Video('123','1','fixture','https://video.twimg.com/a.mp4','https://x.com/fixture/status/123'), self.app.output.get(), 'best')
        self.app._refresh()
        self.app.shell.navigate('downloads')
        self.assertEqual(self.app.shell.current_page, 'downloads')
        self.assertIn(job, self.app.shell.task_cards)
        self.app.shell.select(job)
        self.assertEqual(self.app.tree.selection(), (str(job),))
        self.assertEqual(self.app.shell.stats['account'].get(), '未配置')
        self.app.auth.set('fixture-token')
        self.app.shell.refresh_stats(self.app.store.list())
        self.assertEqual(self.app.shell.stats['account'].get(), '已填写')
        self.assertNotEqual(self.app.shell.stats['account'].get(), '已连接')
        for page in ('overview','archive','organize','settings'):
            self.app.shell.navigate(page)
            self.assertEqual(self.app.shell.current_page, page)

    def test_theme_and_navigation_are_available_while_busy(self):
        self.app._busy(True)
        self.app.set_theme('dark')
        self.app.shell.navigate('organize')
        self.assertEqual(str(self.app.pause_button['state']), 'normal')
        self.assertEqual(self.app.shell.current_page, 'organize')
        self.assertFalse(self.app.shell.new_download_button.instate(['!disabled']) if hasattr(self.app.shell.new_download_button,'instate') else self.app.shell.new_download_button['state']!='disabled')
        self.app._busy(False)

    def test_task_paging_is_bounded_and_destroyed_dialog_controls_are_safe(self):
        for i in range(101):
            self.app.store.enqueue(Video(str(1000+i),'1','fixture','https://video.twimg.com/a.mp4','https://x.com/fixture/status/123'),self.app.output.get(),'best')
        self.app._refresh()
        self.assertEqual(len(self.app.shell.task_cards),100)
        self.app.shell.turn_page(1)
        self.assertEqual(len(self.app.shell.task_cards),1)
        self.app.shell.new_download()
        self.app.shell.dialog.destroy()
        self.app._busy(True)
        self.app.set_theme('dark')
        self.app._busy(False)

    def test_unrelated_refresh_keeps_active_card_progress(self):
        job=self.app.store.enqueue(Video('123','1','fixture','https://video.twimg.com/a.mp4','https://x.com/fixture/status/123'),self.app.output.get(),'best')
        self.app.store.set_state(job,'downloading')
        self.app._refresh()
        self.app.shell.progress(job,50,100,1024)
        self.assertEqual(float(self.app.shell.task_cards[job].bar['value']),50)
        self.app._refresh()
        self.assertEqual(float(self.app.shell.task_cards[job].bar['value']),50)
        self.assertIn('MB/s',self.app.shell.task_cards[job].status['text'])
