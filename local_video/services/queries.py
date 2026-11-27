# SPDX-License-Identifier: GPL-2.0-only
"""Content read models, tags and author preferences. No native UI dependency."""
from .. import __version__
from ..catalog import normalize_tag


class QueryService:
    def __init__(self, store, settings, libraries, runtime, groups=None):
        self.store,self.settings,self.libraries,self.runtime=store,settings,libraries,runtime
        self.groups = groups
        self.local = groups.collection if groups is not None else None

    def resources(self,p,overview=False):
        include_stats = overview and p.get('stats',True)
        include_cards = not overview or p.get('recent',True)
        if not include_stats and not include_cards:
            return dict(resources=[],total=0,offset=0)
        self.store.catalog.ensure_ready()
        outputs=[l['path'] for l in self.libraries.manager.items]
        if not include_cards:
            page = dict(resources=[],total=0,offset=0)
        elif overview:
            page = self.store.resources.page(outputs,limit=8,local_tags=self.local is not None)
        else:
            page = self.store.resources.page(outputs,kind=p.get('kind','all'),query=p.get('query',''),
                                            tags=p.get('tags',[]),offset=p.get('offset',0),limit=p.get('limit',50),
                                            local_tags=self.local is not None)
        for resource in page['resources']:
            for item in resource['locations']:item['error']='' if overview else self.settings.redact(item['error'])
        if include_stats:
            page.update(self.store.resources.summary(outputs))
        return page

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
                'library_cards': self.groups is not None,
                'has_credentials':bool(self.settings.credentials.get('auth_token')),
                'credential_error':self.settings.credential_error,'libraries':libraries,
                'default_library':self.libraries.manager.default_id,'tag_options':tag_options,
                'operation':self.runtime.operation.snapshot(),'busy':self.runtime.busy()}

    @staticmethod
    def empty_page():
        return dict(tasks=[],total=0,offset=0,counts=dict(all=0,pending=0,completed=0,ignored=0))

    def bootstrap(self,p):
        return self.present(self.empty_page(),self.library_rows(),[])

    def library_cards(self, p):
        library = self.libraries.manager.get(p['library_id'])
        self.store.catalog.ensure_ready()
        filters = (p.get('query',''), p.get('tags',[]), p.get('untagged',False))
        offset = max(0,p.get('offset',0))
        limit = min(100,max(1,p.get('limit',50)))
        x_sql, x_args = self.store.catalog.saved_cards_query(library['path'], *filters)
        local_sql, local_args = self.groups.cards_query(library['id'], *filters, archive_order=True)
        # Page over grouped identities so only visible cards need media details.
        cte = f"""WITH x_cards AS ({x_sql}), local_cards AS ({local_sql}), cards AS (
            SELECT 'x' type,tweet_id identifier,'' card_type,added,newest tie_breaker,ignored FROM x_cards
            UNION ALL
            SELECT 'local',identifier,card_type,added,identifier,0 FROM local_cards
        ) """
        args = [*x_args,*local_args]
        with self.store.lock:
            total = self.store.db.execute(cte + 'SELECT COUNT(*) FROM cards', args).fetchone()[0]
            if offset >= total:
                offset = max(0,(total-1)//limit)*limit
            cards = self.store.db.execute(cte + '''SELECT * FROM cards
                ORDER BY added DESC,type DESC,tie_breaker DESC,card_type DESC LIMIT ? OFFSET ?''',
                [*args,limit,offset]).fetchall()
            x_groups = [{'tweet_id':card['identifier'],'ignored':card['ignored']}
                        for card in cards if card['type'] == 'x']
            tasks = self.store.catalog.describe_tasks(x_groups, library['path'], saved_only=True)
            self.redact_task_errors(tasks)
            tasks = {task['id']:task for task in tasks}
            items = [dict(type='x',task=tasks[card['identifier']]) if card['type'] == 'x' else
                     dict(type='local',entry=self.groups.describe_card(library['id'],card)) for card in cards]
        return dict(items=items,total=total,offset=offset)

    def redact_task_errors(self, tasks):
        for task in tasks:
            for item in task['media']:
                item['error'] = self.settings.redact(item['error'])

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
        if collection:
            page['collection_author_count']=self.store.catalog.authors_page(output,limit=1)['total']
        self.redact_task_errors(page['tasks'])
        libraries=self.library_rows()
        local_counts = {row.pop('library'): row for row in self.local.stats()} if self.local is not None else {}
        for library in libraries:
            library.update(self.store.catalog.library_counts(library['path']))
            library.update(self.store.resources.summary([library['path']], include_telegram=False))
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
