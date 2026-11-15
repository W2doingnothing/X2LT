from pathlib import Path
import tempfile
import unittest


class AppTests(unittest.TestCase):
    def test_flatten_button_worker_updates_location_without_losing_completed_state(self):
        import hashlib
        import threading
        import tkinter as tk
        import time
        from local_video.app import App
        from local_video.discovery import Video
        from local_video.runner import destination_for
        from local_video.security import atomic_json
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root = tk.Tk()
            root.withdraw()
            app = App(root, home=home, data_dir=home / 'private')
            output = Path(app.output.get()).resolve()
            job = app.store.enqueue(Video('123', '1', 'alice', 'https://video.twimg.com/a.mp4', 'https://x.com/alice/status/123'), str(output), 'best')
            row = app.store.list()[0]
            old = destination_for(row)
            old.parent.mkdir(parents=True)
            old.write_bytes(b'fixture-video')
            digest = hashlib.sha256(b'fixture-video').hexdigest()
            atomic_json(old.with_suffix('.mp4.verified.json'), {'source': row['url'], 'bytes': 13, 'sha256': digest, 'verification': 'length+sha256'})
            app.store.set_state(job, 'completed', bytes=13, sha256=digest, verification='length+sha256')
            try:
                app.start_flatten(output / '集中视频')
                end = time.monotonic() + 5
                while app.worker.is_alive() and time.monotonic() < end:
                    root.update()
                    time.sleep(.02)
                root.update()
                self.assertEqual(app.store.list()[0]['state'], 'completed')
                self.assertEqual(destination_for(app.store.list()[0]).parent, output / '集中视频')
                self.assertFalse(old.exists())
            finally:
                app.close()

    def test_rollback_worker_and_progress_bars(self):
        import hashlib
        import tkinter as tk
        import time
        from local_video.app import App
        from local_video.discovery import Video
        from local_video.runner import destination_for
        with tempfile.TemporaryDirectory() as tmp:
            root = tk.Tk()
            root.withdraw()
            app = App(root, home=Path(tmp), data_dir=Path(tmp) / 'private')
            try:
                output = Path(app.output.get()).resolve()
                job = app.store.enqueue(Video('123', '1', 'alice', 'https://video.twimg.com/a.mp4', 'https://x.com/alice/status/123'), output, 'best')
                old = destination_for(app.store.list()[0])
                old.parent.mkdir(parents=True)
                old.write_bytes(b'fixture')
                app.store.set_state(job, 'completed', bytes=7, sha256=hashlib.sha256(b'fixture').hexdigest(), verification='length+sha256')
                def wait():
                    end = time.monotonic() + 5
                    while app.worker.is_alive() and time.monotonic() < end:
                        root.update()
                        time.sleep(.02)
                    app._poll()
                app.start_flatten(output / '集中视频')
                wait()
                self.assertEqual(float(app.total_bar['value']), 100)
                self.assertEqual(float(app.file_bar['value']), 100)
                app.start_flatten(None, rollback=True)
                wait()
                self.assertTrue(old.is_file())
                self.assertEqual(destination_for(app.store.list()[0]), old)
                self.assertIn('回退', app.status.get())
                self.assertEqual(float(app.total_bar['value']), 100)
            finally:
                app.close()

    def test_separate_folder_can_import_original_x2t_from_parent(self):
        import tkinter as tk
        from local_video.app import App
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp)
            home = original / 'X2LT'
            home.mkdir()
            (original / 'X2T.exe').write_bytes(b'fixture-only')
            (original / '.env').write_text('X_USER=alice\n', encoding='utf8')
            (original / 'cookies.txt').write_text('.x.com\tTRUE\t/\tTRUE\t0\tauth_token\tfixture-parent-token\n', encoding='utf8')
            root = tk.Tk()
            root.withdraw()
            app = App(root, home=home, data_dir=home / 'private')
            try:
                from unittest.mock import patch
                with patch('local_video.app.messagebox.showerror'):
                    app.import_old(notify=False)
                self.assertEqual(app.auth.get(), 'fixture-parent-token')
                self.assertEqual(app.username.get(), 'alice')
            finally:
                app.close()

    def test_window_can_import_and_save_protected_credentials(self):
        import tkinter as tk
        from local_video.app import App
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'
            home.mkdir()
            (home / '.env').write_text('X_USER=alice\nX_PROXY=http://127.0.0.1:7897\nTG_SESSION=ignored-session\n', encoding='utf8')
            (home / 'cookies.txt').write_text('.x.com\tTRUE\t/\tTRUE\t0\tauth_token\tfixture-ui-token\n.x.com\tTRUE\t/\tTRUE\t0\tct0\tfixture-ui-csrf\n', encoding='utf8')
            root = tk.Tk()
            root.withdraw()
            app = App(root, home=home, data_dir=Path(tmp) / 'private')
            app.import_old(notify=False)
            root.update()
            self.assertEqual(app.username.get(), 'alice')
            self.assertEqual(app.vault.load()['auth_token'], 'fixture-ui-token')
            self.assertNotIn(b'fixture-ui-token', app.vault.path.read_bytes())
            self.assertNotIn('ignored-session', str(app.vault.load()))
            from local_video.discovery import Video
            job = app.store.enqueue(Video('123', '1', 'alice', 'https://video.twimg.com/a.mp4', 'https://x.com/alice/status/123'), str(home), 'best')
            app.store.set_state(job, 'completed', bytes=4, sha256='x', verification='sha256-only')
            app._refresh()
            self.assertNotIn('已校验长度', app.tree.set(str(job), 'detail'))
            self.assertIn('缺少', app.tree.set(str(job), 'detail'))
            app.close()


if __name__ == '__main__':
    unittest.main()
