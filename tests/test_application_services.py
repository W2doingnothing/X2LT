import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from local_video.services.settings import SettingsService
from local_video.services.runtime import WorkerRuntime
from local_video.services.downloads import DownloadService


class ApplicationServiceTests(unittest.TestCase):
    def test_app_icon_defaults_validates_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'settings.json'
            vault = Mock()
            vault.load.return_value = {}
            path.write_text('{"theme":"dark"}', encoding='utf8')
            settings = SettingsService(path, root, vault)
            self.assertEqual(settings.public()['app_icon'], 'gray-pixel')
            for icon in ('gray-illustration', 'black-white-pixel', 'black-white-illustration', 'gray-pixel'):
                saved = settings.save({'app_icon': icon})
                self.assertEqual(saved['app_icon'], icon)
                self.assertEqual(SettingsService(path, root, vault).public()['app_icon'], icon)
            with self.assertRaises(ValueError):
                settings.save({'app_icon': '../external'})
            self.assertEqual(settings.public()['app_icon'], 'gray-pixel')
            path.write_text('{"app_icon":["unknown"]}', encoding='utf8')
            self.assertEqual(SettingsService(path, root, vault).public()['app_icon'], 'gray-pixel')

    def test_updates_publish_snapshots_without_mutating_existing_readers(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault=Mock();vault.load.return_value={}
            settings=SettingsService(Path(tmp)/'settings.json',Path(tmp),vault)
            previous_values=settings.values
            previous_credentials=settings.credentials
            settings.save({'theme':'dark'})
            settings.update_credentials({'auth_token':'synthetic-secret'})
            self.assertEqual(previous_values['theme'],'light')
            self.assertEqual(previous_credentials,{})
            self.assertEqual(settings.values['theme'],'dark')
            self.assertEqual(settings.credentials['auth_token'],'synthetic-secret')

    def test_settings_are_headless_and_credentials_never_enter_public_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            vault=Mock();vault.load.return_value={}
            settings=SettingsService(root/'settings.json',root,vault)
            settings.update_credentials({'auth_token':'synthetic-secret','proxy':''})
            settings.save({'theme':'dark','confirm_removal':False})
            self.assertEqual(settings.public()['theme'],'dark')
            self.assertFalse(settings.public()['confirm_removal'])
            self.assertNotIn('synthetic-secret',json.dumps(settings.public()))
            self.assertNotIn('auth_token',(root/'settings.json').read_text())
            vault.save.assert_called_once_with({'auth_token':'synthetic-secret','proxy':''})
            with self.assertRaises(ValueError):settings.save({'connections':True})
            with self.assertRaises(ValueError):settings.update_credentials({'unexpected':'value'})

    def test_failed_vault_read_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault=Mock();vault.load.side_effect=OSError('synthetic-secret')
            settings=SettingsService(Path(tmp)/'settings.json',Path(tmp),vault)
            self.assertTrue(settings.credential_error)
            self.assertNotIn('synthetic-secret',settings.credential_error)
            self.assertEqual(settings.credentials,{})
            vault.save.assert_not_called()

    def test_download_worker_keeps_original_runner_and_settings_snapshot(self):
        captured=[]
        class Thread:
            def __init__(self,target,**kw):captured.append(target)
            def start(self):pass
            def is_alive(self):return False
        runtime=WorkerRuntime(thread_factory=Thread)
        store=Mock();settings=Mock();libraries=Mock()
        settings.values={'username':'fixture','quality':'best'}
        settings.credentials={'auth_token':'synthetic-token','proxy':''}
        libraries.manager.get.return_value={'id':'a','path':'F:\\Synthetic'}
        libraries.errors={}
        first=Mock();factory=Mock(return_value=first)
        service=DownloadService(store,settings,libraries,runtime,runner_factory=factory)
        service.start({'mode':'continue','library_id':'a'})
        settings.values['quality']='480p';settings.credentials['auth_token']='new-token'
        runtime.runner=Mock()
        captured[0]()
        first.run.assert_called_once()
        runtime.runner.run.assert_not_called()
        self.assertEqual(factory.call_args.args[1]['quality'],'best')
        self.assertEqual(factory.call_args.args[2]['auth_token'],'synthetic-token')


if __name__=='__main__':unittest.main()
