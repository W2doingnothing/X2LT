import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('release_tools', Path(__file__).resolve().parent.parent / 'scripts/prepare-release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_dirty_or_stale_build_cannot_be_packaged_as_current_source(self):
        good = {'commit': 'abc', 'version': '0.3.2', 'clean': True, 'artifacts': {'app.exe': 'hash'}}
        release.validate_build_info(good, 'abc', '0.3.2', {'app.exe': 'hash'})
        for changed in ({'clean': False}, {'commit': 'old'}, {'version': 'old'}, {'artifacts': {'app.exe': 'tampered'}}):
            with self.assertRaises(RuntimeError):
                release.validate_build_info({**good, **changed}, 'abc', '0.3.2', {'app.exe': 'hash'})

    def test_dependency_drift_blocks_release_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'requirements-local-video-lock.txt').write_text('requests==2.32.5\n', encoding='utf8')
            with patch.object(release, 'ROOT', root), patch('importlib.metadata.version', return_value='9.9.9'):
                with self.assertRaises(RuntimeError):
                    release.verify_dependencies()

    def test_uncommitted_source_blocks_release_packaging(self):
        with patch.object(release.subprocess, 'check_output', return_value=b' M README.md\n'):
            with self.assertRaises(RuntimeError):
                release.require_clean()

    def test_source_zip_uses_manifest_allowlist_instead_of_whole_cache(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / 'cache'
            cache.mkdir()
            (cache / 'SOURCES.json').write_text('[]')
            (cache / 'expected.tar.gz').write_bytes(b'fixture')
            (cache / 'obsolete.tar.gz').write_bytes(b'old fixture')
            release.zip_sources(cache, root / 'sources.zip', [{'file': 'expected.tar.gz'}])
            with zipfile.ZipFile(root / 'sources.zip') as archive:
                self.assertEqual(set(archive.namelist()), {'SOURCES.json', 'expected.tar.gz'})
