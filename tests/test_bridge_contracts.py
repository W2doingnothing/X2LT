import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock

from local_video.desktop_service import DesktopService
from local_video.bridge_contracts import validate_request


class BridgeContractTests(unittest.TestCase):
    def test_cloud_card_appearance_persists_as_local_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, home = Path(tmp)/'data', Path(tmp)/'home'
            service = DesktopService(data,home)
            try:
                saved = service.call('settings',{'cloud_card_name':'  云盘收藏  ','cloud_card_emoji':'📚'})
                self.assertTrue(saved['ok'])
                self.assertFalse(service.call('settings',{'cloud_card_emoji':'plain text'})['ok'])
            finally:
                service._close()
            service = DesktopService(data,home)
            try:
                settings = service.call('snapshot')['data']['settings']
                self.assertEqual((settings['cloud_card_name'],settings['cloud_card_emoji']),('云盘收藏','📚'))
            finally:
                service._close()

    def test_credential_write_error_redacts_new_unsaved_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=DesktopService(Path(tmp)/'data',Path(tmp)/'home')
            try:
                service._vault.save=Mock(side_effect=OSError('cannot save synthetic-new-secret'))
                response=service.call('credentials',{'auth_token':'synthetic-new-secret'})
                self.assertFalse(response['ok'])
                self.assertNotIn('synthetic-new-secret',response['error'])
                self.assertNotIn('auth_token',service._credentials)
            finally:service._close()

    def test_existing_frontend_request_shapes_remain_accepted(self):
        requests = {
            'snapshot': {'filter':'completed','query':'测试','offset':50,'limit':50,'library_id':'main','tags':['标签'],'untagged':False,'collection':True,'view':'authors','author_key':'id:42','author_group':'archive'},
            'author_preferences':{'library_id':'main','keys':['id:42'],'restore':[{'key':'id:42','starred':False,'in_other':False}]},
            'start':{'mode':'force','library_id':'main','job_id':1},
            'organize':{'library_id':'main','rollback':True,'search':True,'custom_target':False},
            'library_update':{'id':'main','path':None,'quota_bytes':0,'emoji':'📚'},
            'removal_plan':{'ids':['123'],'library_id':'main'},
            'filters_clear':{'all':True},'library_cover':{'id':'main','kind':'image'},
            'preview':{'job_id':1},'credentials':{'auth_token':'synthetic','ct0':'','proxy':''},
        }
        for action,payload in requests.items():
            with self.subTest(action=action):self.assertEqual(validate_request(action,payload),payload)
        request={'ids':['123'],'ignored':True}
        validated=validate_request('ignore',request)
        request['ids'].append('456')
        self.assertEqual(validated['ids'],['123'])

    def test_author_preference_contract_preserves_existing_100_item_limit(self):
        keys=['id:'+str(i) for i in range(100)]
        restore=[{'key':key,'starred':False,'in_other':False} for key in keys]
        payload={'library_id':'main','keys':keys,'restore':restore}
        self.assertEqual(validate_request('author_preferences',payload),payload)
        with self.assertRaises(ValueError):validate_request('author_preferences',{'library_id':'main','keys':keys+['id:101']})

    def test_invalid_payload_shapes_and_types_are_rejected(self):
        for payload in (False,0,'',[],['x']):
            with self.subTest(payload=payload),self.assertRaises(ValueError):validate_request('snapshot',payload)
        for action,payload in [('snapshot',{'offset':True}),('start',{'mode':'force','job_id':True}),
                               ('ignore',{'ids':['123'],'ignored':'false'}),('library_remove',{'id':123}),
                               ('filters_clear',{'all':'true'}),('settings',{'connections':True}),
                               ('credentials',{'unknown':'secret'}),('poll',{'auth_token':'secret'})]:
            with self.subTest(action=action),self.assertRaises(ValueError):validate_request(action,payload)
        self.assertEqual(validate_request('snapshot',None),{})
        self.assertEqual(validate_request('snapshot',{'offset':50,'collection':True,'library_id':'main'}),{'offset':50,'collection':True,'library_id':'main'})

    def test_invalid_credentials_request_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=DesktopService(Path(tmp)/'data',Path(tmp)/'home')
            try:
                service._vault.save=Mock()
                for payload in ([],False,{'auth_token':'synthetic-secret','unknown':'bad'}):
                    response=service.call('credentials',payload)
                    self.assertFalse(response['ok'])
                    self.assertNotIn('synthetic-secret',response['error'])
                service._vault.save.assert_not_called()
            finally:service._close()

    def test_router_registry_matches_contracts_and_compatibility_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=DesktopService(Path(tmp)/'data',Path(tmp)/'home')
            try:
                from local_video.bridge_contracts import ACTIONS
                self.assertEqual(set(service._handlers),set(ACTIONS))
                self.assertEqual(set(service.call('snapshot')),{'ok','data'})
                self.assertEqual(set(service.call('nonexistent')),{'ok','error'})
            finally:service._close()


if __name__=='__main__':unittest.main()
