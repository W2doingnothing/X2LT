# SPDX-License-Identifier: GPL-2.0-only
"""Content read models, tags and author preferences. No native UI dependency."""
from .. import __version__
from ..catalog import normalize_tag


class QueryService:
    def __init__(self, store, settings, libraries, runtime, local=None):
        self.store,self.settings,self.libraries,self.runtime=store,settings,libraries,runtime
        self.local = local

    def resources(self,p,overview=False):
        self.store.catalog.ensure_ready()
        outputs=[l['path'] for l in self.libraries.manager.items]
        page=self.store.resources.page(outputs,limit=8) if overview else self.store.resources.page(outputs,kind=p.get('kind','all'),query=p.get('query',''),tags=p.get('tags',[]),offset=p.get('offset',0),limit=p.get('limit',50))
        for resource in page['resources']:
            for item in resource['locations']:item['error']='' if overview else self.settings.redact(item['error'])
        return {**self.store.resources.summary(outputs),**page} if overview else page

    def author_preferences(self,p):
        if not p.get('library_id'):raise ValueError('请选择媒体库')
        library=self.libraries.manager.get(p['library_id'])
        before=self.store.catalog.author_preferences(library['id'],library['path'],p.get('keys'),starred=p.get('starred'),in_other=p.get('archived',p.get('in_other')),restore=p.get('restore'))
        self.runtime.emit({'type':'changed'})
        return before

    def set_tags(self,p):
        self.store.catalog.set_tags(p['id'],p['tags']);self.runtime.emit({'type':'changed'});return True

    def rename_tag(self,p):
        key = normalize_tag(p['old'])[0]
        if self.local is None or self.local.one('SELECT 1 FROM catalog_tags WHERE normalized=?', (key,)):
            self.store.catalog.rename_tag(p['old'],p['new'])
        if self.local is not None:
            self.local.rename_tag(p['old'],p['new'])
        self.runtime.emit({'type':'changed'})
        return True

    def delete_tag(self,p):
        self.store.catalog.delete_tag(p['name'])
        if self.local is not None:
            self.local.delete_tag(p['name'])
        self.runtime.emit({'type':'changed'})
        return True

    def capacity_changed(self):
        self.runtime.emit({'type':'changed'})

    def library_rows(self):
        manager = self.libraries.manager
        rows = manager.snapshot(cached_only=True)
        for library in rows:
            error = self.libraries.errors.get(library['id'])
            if error:
                library.update(online=False,capacity_pending=False,error=error)
        manager.refresh_background(self.capacity_changed)
        return rows

    def present(self,page,libraries,tag_options):
        return {**page,'version':__version__,'settings':self.settings.public(),
                'has_credentials':bool(self.settings.credentials.get('auth_token')),
                'credential_error':self.settings.credential_error,'libraries':libraries,
                'default_library':self.libraries.manager.default_id,'tag_options':tag_options,
                'operation':self.runtime.operation.snapshot(),'busy':self.runtime.busy()}

    @staticmethod
    def empty_page():
        return dict(tasks=[],total=0,offset=0,counts=dict(all=0,pending=0,completed=0,ignored=0))

    def bootstrap(self,p):
        return self.present(self.empty_page(),self.library_rows(),[])

    def snapshot(self,p):
        self.store.catalog.ensure_ready()
        output=self.libraries.manager.get(p['library_id'])['path'] if p.get('library_id') else None
        collection=bool(p.get('collection'))
        if collection and output is None:raise ValueError('归档浏览需要媒体库')
        params=dict(query=str(p.get('query',''))[:200],output=output,tags=p.get('tags',[]),untagged=bool(p.get('untagged',False)),offset=max(0,int(p.get('offset',0))),limit=min(100,max(1,int(p.get('limit',50)))))
        if p.get('summary_only'):
            page=self.empty_page()
        elif collection and p.get('view')=='sources':
            page={**self.store.catalog.sources_page(**params),'tasks':[],'counts':dict(all=0,pending=0,completed=0,ignored=0)}
        elif collection and p.get('view')=='authors':
            page={**self.store.catalog.authors_page(**params,library_id=p['library_id'],group=p.get('author_group','root')),'tasks':[],'counts':dict(all=0,pending=0,completed=0,ignored=0)}
        else:
            page=self.store.catalog.page(**params,filter=p.get('filter','all'),saved_only=collection,author_key=str(p.get('author_key',''))[:250] if collection else '',source_key=str(p.get('source_key',''))[:250] if collection else '')
        if collection and not p.get('summary_only'):
            page['collection_author_count']=self.store.catalog.authors_page(output,limit=1)['total']
        for task in page['tasks']:
            for item in task['media']:item['error']=self.settings.redact(item['error'])
        libraries=self.library_rows()
        local_counts = {row.pop('library'): row for row in self.local.stats()} if self.local is not None else {}
        for library in libraries:
            library.update(self.store.catalog.library_counts(library['path']))
            library.update(self.store.resources.summary([library['path']]))
            for key, value in local_counts.get(library['id'], {}).items():
                library[key] = library.get(key, 0) + value
        tag_options = self.store.catalog.tags(output,saved_only=collection)
        if collection and self.local is not None:
            tags = {normalize_tag(row['name'])[0]:row for row in tag_options}
            for row in self.local.tag_options(p['library_id']):
                key = normalize_tag(row['name'])[0]
                if key in tags:
                    tags[key]['count'] += row['count']
                else:
                    tags[key] = row
            tag_options = [tags[key] for key in sorted(tags)]
        return self.present(page,libraries,tag_options)
