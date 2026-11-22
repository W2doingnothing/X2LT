# SPDX-License-Identifier: GPL-2.0-only
"""Fixed bridge command shapes. No reflection, coercion, or unknown fields."""
from dataclasses import dataclass
from typing import Any, Literal, TypedDict
from .services.settings import DEFAULTS


class SuccessResponse(TypedDict):
    ok: Literal[True]
    data: Any


class ErrorResponse(TypedDict):
    ok: Literal[False]
    error: str


BridgeResponse = SuccessResponse | ErrorResponse


@dataclass(frozen=True)
class Field:
    kind: type
    required: bool = False
    nullable: bool = False
    items: type | None = None
    max_length: int = 8192

    def check(self, value):
        if value is None and self.nullable: return True
        if type(value) is not self.kind: return False
        if isinstance(value,(str,list)) and len(value)>self.max_length: return False
        if self.kind is list and self.items is not None:
            return all(type(item) is self.items and (not isinstance(item,str) or len(item)<=8192) for item in value)
        return True


S=Field(str); B=Field(bool); I=Field(int)
ID=Field(str,required=True); JOB=Field(int,required=True)
OPT=Field(str,nullable=True)
STRINGS=Field(list,items=str,max_length=10000)
TAGS=Field(list,items=str,max_length=20)
IDS=Field(list,required=True,items=str,max_length=50)
SNAPSHOT=dict(filter=S,query=S,offset=I,limit=I,library_id=OPT,tags=TAGS,untagged=B,collection=B,
              view=S,source_key=OPT,author_key=OPT,author_group=S)
SETTING_FIELDS={key:Field(type(value)) for key,value in DEFAULTS.items()}
ACTIONS={
    'snapshot':SNAPSHOT,
    'filters':dict(query=S,offset=I),
    'filters_clear':dict(keys=STRINGS,all=B),
    'removal_plan':dict(ids=IDS,library_id=ID),
    'removal_start':dict(ids=IDS,library_id=ID),
    'removal_status':dict(id=ID),'removal_retry':dict(id=ID),
    'resources':dict(kind=S,query=S,tags=TAGS,offset=I,limit=I), 'resource_overview':{},
    'author_preferences':dict(library_id=ID,keys=Field(list,required=True,items=str,max_length=100),starred=B,in_other=B,archived=B,restore=Field(list,items=dict,max_length=100)),
    'poll':{}, 'tags_set':dict(id=ID,tags=Field(list,required=True,items=str,max_length=20)),
    'tag_rename':dict(old=ID,new=ID),'tag_delete':dict(name=ID),
    'library_cover':dict(id=ID,kind=ID,value=S,emoji=S),
    'settings':SETTING_FIELDS,
    'credentials':dict(auth_token=S,ct0=S,proxy=S),'import_legacy':{},'choose_folder':{},
    'library_add':dict(name=ID,path=ID,quota_bytes=I,emoji=S),
    'library_update':dict(id=ID,name=OPT,path=OPT,quota_bytes=Field(int,nullable=True),emoji=OPT),
    'library_default':dict(id=ID),'library_remove':dict(id=ID),'library_refresh':{},
    'ignore':dict(ids=IDS,ignored=B),'pause':{},
    'start':dict(mode=S,library_id=OPT,url=S,job_id=I),
    'organize':dict(library_id=OPT,rollback=B,search=B,custom_target=B),
    'open_profile':dict(handle=ID),'open_tweet':dict(job_id=JOB),
    'open_folder':dict(job_id=JOB),'open_media':dict(job_id=JOB),
    'clear_previews':{},'preview':dict(job_id=JOB),'avatar':dict(id=ID),'cover':dict(id=ID),
    'local_pick':dict(kind=ID,selection=ID),
    'local_scan':dict(selection=ID,library_id=ID,kind=ID,storage=ID,recursive=B,batch=B),
    'local_import_status':dict(id=ID),'local_import_cancel':dict(id=ID),
    'local_import_start':dict(id=ID,ids=Field(list,items=str,max_length=25000)),
    'local_import_retry':dict(id=ID,item_id=S),
    'local_library':dict(library_id=ID,query=S,tags=TAGS,untagged=B,offset=I,limit=I),
    'local_media_open':dict(id=JOB,location_id=I,folder=B),
    'local_media_preview':dict(id=JOB,large=B),
    'local_media_tags':dict(id=JOB,tags=Field(list,required=True,items=str,max_length=20)),
    'local_media_link':dict(id=JOB,library_id=ID,linked=Field(bool,required=True)),
    'local_media_remove':dict(id=JOB,library_id=ID,location_id=I,recycle=B),
    'local_media_relocate':dict(id=JOB,location_id=JOB),
    'local_comic_get':dict(id=JOB),
    'local_comic_page':dict(id=JOB,page_id=ID,thumbnail=B),
    'local_comic_update':dict(id=JOB,page=I,spread=B,rtl=B,order=Field(list,items=str,max_length=20000),
                              cover=S,custom_cover=B),
    'cloud_status':{}, 'cloud_login':{}, 'cloud_list':dict(parent=S,page=I,refresh=B),
    'cloud_import':dict(parent=ID,name=S,recursive=B),
    'cloud_catalog':dict(library_id=S,query=S,tag=S,offset=I),
    'cloud_tags':dict(id=JOB,tags=Field(list,required=True,items=str,max_length=20)),
    'cloud_link':dict(id=JOB,library_id=ID,linked=Field(bool,required=True)),
    'cloud_positions':dict(ids=Field(list,required=True,items=int,max_length=1000)),
    'cloud_upload':dict(ids=Field(list,required=True,items=int,max_length=100),parent=ID),
    'cloud_archive_plan':dict(library_id=ID,parent=ID,ids=Field(list,items=str,max_length=100),
                              mode=ID,threshold=I,aliases=Field(dict),tag_overrides=Field(dict)),
    'cloud_archive_start':dict(id=ID),
    'cloud_locate':dict(id=JOB), 'cloud_open':dict(id=JOB),
    'cloud_preview':dict(id=JOB), 'cloud_source':dict(id=JOB), 'cloud_check':dict(id=JOB),
    'cloud_thumbnail':dict(key=ID), 'cloud_preview_credentials':dict(client_id=ID,client_secret=ID),
    'cloud_media_view':dict(key=S,local_id=I,retry=B),
    'cloud_playback_status':{}, 'cloud_playback_connect':dict(client_id=ID,redirect=ID),
    'cloud_download':dict(id=JOB,library_id=ID), 'cloud_release':dict(id=JOB),
    'cloud_jobs':{}, 'cloud_retry':dict(id=S,all=B), 'cloud_cancel':dict(id=S,all=B),
    'cloud_backup':{}, 'cloud_restore':{},
}


def validate_request(action, payload):
    if not isinstance(action,str) or action not in ACTIONS: raise ValueError('不支持的操作')
    if payload is None: payload={}
    if type(payload) is not dict: raise ValueError('请求格式无效')
    fields=ACTIONS[action]
    if set(payload)-fields.keys(): raise ValueError('请求含不支持的字段')
    for key,field in fields.items():
        if key not in payload:
            if field.required: raise ValueError('请求缺少必要字段')
        elif not field.check(payload[key]): raise ValueError('请求字段格式无效')
    # Snapshot mutable lists/dictionaries so callers cannot change accepted requests in flight.
    from copy import deepcopy
    return deepcopy(payload)
