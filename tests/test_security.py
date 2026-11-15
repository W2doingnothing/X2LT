import json
import os
from pathlib import Path
import tempfile
import unittest

from local_video.security import Vault, import_legacy, protect, redact, unprotect
from local_video.policy import validate_media_url, validate_source_url


class SecurityTests(unittest.TestCase):
    def test_dpapi_roundtrip_and_tamper(self):
        secret = b'auth_token=local-test-secret-not-a-real-cookie'
        blob = protect(secret)
        self.assertNotIn(secret, blob)
        self.assertEqual(unprotect(blob), secret)
        with self.assertRaises(OSError):
            unprotect(blob[:-12] + b'broken')

    def test_vault_persists_no_plaintext(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Vault(Path(tmp) / 'secrets.bin')
            vault.save({'auth_token': 'fixture-secret-123', 'ct0': 'fixture-csrf'})
            self.assertNotIn(b'fixture-secret', vault.path.read_bytes())
            self.assertEqual(vault.load()['ct0'], 'fixture-csrf')

    def test_legacy_import_only_x_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.env').write_text('X_USER=example\nX_PROXY=socks5://127.0.0.1:7897\nTG_SESSION=do-not-import\nTG_API_HASH=do-not-import\n', encoding='utf8')
            (root / 'cookies.txt').write_text('.x.com\tTRUE\t/\tTRUE\t0\tauth_token\tfixture-token\n.x.com\tTRUE\t/\tTRUE\t0\tct0\tfixture-csrf\n', encoding='utf8')
            data = import_legacy(root)
            self.assertEqual(data['username'], 'example')
            self.assertEqual(data['auth_token'], 'fixture-token')
            self.assertNotIn('TG_SESSION', json.dumps(data))
            self.assertNotIn('do-not-import', json.dumps(data))

    def test_source_policy_rejects_deceptive_urls(self):
        for url in ['https://x.com.evil.test/a/status/123', 'https://user:pass@x.com/a/status/123', 'http://x.com/a/status/123', 'https://x.com/a/status/123/../../evil']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_source_url(url)
        self.assertEqual(validate_source_url('https://twitter.com/example/status/123?s=20'), 'https://x.com/example/status/123')

    def test_media_policy_rejects_private_and_non_media_hosts(self):
        for url in ['http://video.twimg.com/a.mp4', 'https://127.0.0.1/a.mp4', 'https://video.twimg.com.evil.test/a.mp4', 'https://x.com/a.mp4', 'https://u:p@video.twimg.com/a.mp4', 'https://video.twimg.com:8443/a.mp4']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_media_url(url)
        self.assertEqual(validate_media_url('https://video.twimg.com/a.mp4?tag=12'), 'https://video.twimg.com/a.mp4?tag=12')

    def test_redaction_hides_secrets_and_url_queries(self):
        message = redact('auth_token=abcdef ct0=xyz socks5://user:password@127.0.0.1:7897 https://video.twimg.com/a.mp4?secret=123', ['abcdef', 'xyz'])
        for value in ['abcdef', 'xyz', 'password', 'secret=123']:
            self.assertNotIn(value, message)


if __name__ == '__main__':
    unittest.main()
