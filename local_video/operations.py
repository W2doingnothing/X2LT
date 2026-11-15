# SPDX-License-Identifier: GPL-2.0-only
"""Recoverable UI operation state, independent of the lossy event queue."""
import threading
import uuid


class OperationState:
    def __init__(self):
        self.lock=threading.RLock();self.value=None

    def begin(self,reading=False,label='下载'):
        with self.lock:
            run_id=uuid.uuid4().hex
            self.value={'run_id':run_id,'phase':'reading' if reading else 'downloading','reading':bool(reading),
                'discovered_tweets':0,'discovered_media':0,'queued':0,'processed':0,'successful':0,'failed':0,'skipped':0,'total':None if reading else 0,'message':label}
            return run_id

    def update(self,event):
        with self.lock:
            if self.value is None or event.get('run_id')!=self.value['run_id']:return
            v=self.value;kind=event.get('type')
            if kind=='run_stats':
                for key in ('discovered_tweets','discovered_media','queued','processed','successful','failed','skipped'):
                    if key in event:v[key]=max(v[key],max(0,int(event[key])))
                if 'reading' in event:v['reading']=bool(event['reading'])
                if not v['reading'] and 'total' in event:v['total']=max(0,int(event['total']))
                if v['phase'] not in ('paused','failed'):v['phase']='reading' if v['reading'] else 'downloading'
            elif kind in ('run_paused','run_failed'):
                v['phase']='paused' if kind=='run_paused' else 'failed';v['message']=str(event.get('text',''))[:1000]
            elif kind=='finished':
                v['reading']=False
                if v['phase'] not in ('paused','failed'):v['phase']='failed' if v['failed'] else 'completed'

    def snapshot(self):
        with self.lock:return dict(self.value) if self.value is not None else None
