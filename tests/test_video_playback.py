# SPDX-License-Identifier: GPL-2.0-only
import hashlib
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from urllib.parse import urlsplit

from local_video.archive import original_path
from local_video.desktop_service import DesktopService
from local_video.discovery import Video


class PlaybackTests(unittest.TestCase):
    origin = 'http://127.0.0.1:14444'
    document = origin + '/index.html?desktop=1'
    fixture = bytes(range(256)) * 16

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.service = DesktopService(self.root/'data', self.root/'home')
        self.addCleanup(self.service._close)
        self.service._window = SimpleNamespace(real_url=self.document, get_current_url=lambda: self.document)
        self.library = self.service._libraries.get()

    def add_local(self, name='fixture.mp4', kind='video'):
        path = self.root / name
        path.write_bytes(self.fixture)
        entry = self.service._local.collection.add(
            dict(kind=kind,title=path.stem,extension=path.suffix[1:],bytes=len(self.fixture),pages=[]),
            hashlib.sha256(self.fixture).hexdigest(),path,self.root,self.library['id'],False,
        )
        return entry,path

    def open_video(self, entry, source='local'):
        ticket = self.service.call('video_open',{'source':source,'id':entry})
        self.assertTrue(ticket['ok'],ticket)
        self.assertEqual(set(ticket['data']),{'url','token'})
        self.assertNotIn(str(self.root),ticket['data']['url'])
        return ticket['data']

    def request(self, url, method='GET', headers=None):
        target = urlsplit(url)
        client = HTTPConnection(target.hostname,target.port,timeout=3)
        try:
            client.request(method,target.path,headers={'Origin':self.origin} if headers is None else headers)
            with client.getresponse() as response:
                return response.status,dict(response.getheaders()),response.read()
        finally:
            client.close()

    def test_range_head_and_revoke(self):
        entry,_ = self.add_local()
        ticket = self.open_video(entry)
        for value,data,content_range in [
            ('bytes=100-127',self.fixture[100:128],'bytes 100-127/4096'),
            ('bytes=4094-',self.fixture[-2:],'bytes 4094-4095/4096'),
            ('bytes=-3',self.fixture[-3:],'bytes 4093-4095/4096'),
        ]:
            status,headers,body = self.request(ticket['url'],headers={'Origin':self.origin,'Range':value})
            self.assertEqual((status,body),(206,data))
            self.assertEqual(headers['Accept-Ranges'],'bytes')
            self.assertEqual(headers['Content-Range'],content_range)
            self.assertEqual(headers['Access-Control-Allow-Origin'],self.origin)
        for value in ('bytes=4096-','bytes=3-2','bytes=0-2,3-4','bytes=-0'):
            status, headers, _ = self.request(ticket['url'],headers={'Origin':self.origin,'Range':value})
            self.assertEqual(status,416)
            self.assertEqual(headers['Content-Range'],'bytes */4096')
        status,headers,body = self.request(ticket['url'],method='HEAD')
        self.assertEqual((status,headers['Content-Length'],headers['Content-Type'],body),(200,'4096','video/mp4',b''))
        status,headers,body = self.request(ticket['url'],method='OPTIONS',headers={
            'Origin':self.origin, 'Access-Control-Request-Method':'GET', 'Access-Control-Request-Headers':'range',
        })
        self.assertEqual((status,headers['Access-Control-Allow-Headers'],body),(204,'Range',b''))
        self.assertTrue(self.service.call('video_close',{'token':ticket['token']})['ok'])
        self.assertEqual(self.request(ticket['url'])[0],404)

    def test_origin_validation_and_replacement(self):
        entry,path = self.add_local()
        ticket = self.open_video(entry)
        for headers in (
            {}, {'Origin':'https://evil.invalid'}, {'Origin':'null'},
            {'Referer':self.document.replace('index.html','index.html.evil')},
            {'Origin':self.origin,'Host':'evil.invalid'},
        ):
            self.assertEqual(self.request(ticket['url'],headers=headers)[0],403)
        self.assertEqual(self.request(ticket['url'],headers={'Referer':self.document})[2],self.fixture)
        self.assertEqual(self.request(ticket['url'],headers={'Referer':self.origin+'/'})[2],self.fixture)
        path.write_bytes(b'replacement')
        self.assertEqual(self.request(ticket['url'])[0],404)
        self.assertFalse(self.service.call('video_open',{'source':'local','id':entry})['ok'])

    def test_only_completed_video_records_issue_tickets(self):
        image,_ = self.add_local('fixture.png','image')
        self.assertFalse(self.service.call('video_open',{'source':'local','id':image})['ok'])
        self.assertFalse(self.service.call('video_open',{'source':'local','id':image,'path':str(self.root)})['ok'])
        movie,_ = self.add_local()
        self.assertFalse(self.service.call('video_open',{'source':'local','id':movie,'location_id':-1})['ok'])
        job = self.service._store.enqueue(
            Video('123','0','fixture','https://video.twimg.com/fixture.mp4','https://x.com/fixture/status/123'),
            self.library['path'],'best',
        )
        row = self.service._store.get_many([job])[0]
        path = original_path(row)
        path.parent.mkdir(parents=True)
        path.write_bytes(self.fixture)
        self.assertFalse(self.service.call('video_open',{'source':'x','id':job})['ok'])
        self.service._store.set_state(job,'completed',bytes=len(self.fixture),sha256=hashlib.sha256(self.fixture).hexdigest(),
                                      verification='length+sha256')
        self.assertEqual(self.request(self.open_video(job,'x')['url'])[2],self.fixture)
