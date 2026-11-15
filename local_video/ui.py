# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Card dashboard and navigation; all worker orchestration remains in App."""
from pathlib import Path
import math
import tkinter as tk
from tkinter import ttk
from .themes import PALETTES
from .policy import REQUEST_STRATEGIES
from . import __version__

FONT = 'Microsoft YaHei UI'
PAGES = {'overview':'总览','downloads':'下载任务','archive':'归档记录','organize':'视频整理','settings':'设置'}


class Card(tk.Canvas):
    def __init__(self, shell, parent, height=130):
        super().__init__(parent, height=height, highlightthickness=0, bd=0)
        self.shell = shell
        self.body = tk.Frame(self, bd=0)
        self.window = self.create_window(17, 14, anchor='nw', window=self.body)
        self.bind('<Configure>', self.draw)
        self.body.bind('<Configure>', self.fit)
        shell.cards.append(self)

    def fit(self, event=None):
        height = self.body.winfo_reqheight() + 30
        if height != self.winfo_reqheight():
            self.configure(height=height)
        self.draw()

    def draw(self, event=None):
        p = self.shell.palette
        self.configure(bg=p['bg'])
        self.body.configure(bg=p['card'])
        w, h, r = max(40, self.winfo_width()), max(40, self.winfo_height()), 18
        self.delete('surface')
        points=[]
        for cx,cy,start in ((w-r-1,r+1,-90),(w-r-1,h-r-1,0),(r+1,h-r-1,90),(r+1,r+1,180)):
            for step in range(9):
                angle=math.radians(start+step*90/8)
                points.extend((cx+r*math.cos(angle),cy+r*math.sin(angle)))
        self.create_polygon(points,fill=p['card'],outline=p['line'],tags='surface')
        self.tag_lower('surface')
        self.itemconfigure(self.window, width=max(20,w-34))


class ScrollArea(tk.Frame):
    def __init__(self, shell, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(self, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')
        self.body = tk.Frame(self.canvas)
        self.window = self.canvas.create_window(0,0,window=self.body,anchor='nw')
        self.body.bind('<Configure>', lambda e:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e:self.canvas.itemconfigure(self.window,width=e.width))
        # Scope mouse-wheel handling to this area; never replace global bindings.
        self.wheel_binding = self.winfo_toplevel().bind('<MouseWheel>',self.wheel,add='+')
        shell.backgrounds.extend([(self,'bg'),(self.canvas,'bg'),(self.body,'bg')])

    def wheel(self,event):
        x,y=self.winfo_pointerxy()
        if self.winfo_rootx()<=x<=self.winfo_rootx()+self.winfo_width() and self.winfo_rooty()<=y<=self.winfo_rooty()+self.winfo_height():
            self.canvas.yview_scroll(int(-event.delta/120),'units')

    def leave(self,event):
        pass


class TaskCard:
    def __init__(self, shell, parent, row):
        self.shell, self.row = shell, row
        self.card = Card(shell,parent)
        self.card.pack(fill='x',pady=(0,10))
        b=self.card.body
        b.columnconfigure(1,weight=1)
        badge=shell.label(b,'MP4',role='accent',font=(FONT,12,'bold'))
        badge.grid(row=0,column=0,rowspan=3,padx=(0,18),sticky='n',pady=8)
        self.title=shell.label(b,'',font=(FONT,11,'bold'));self.title.grid(row=0,column=1,sticky='w')
        self.details=shell.label(b,'',role='muted');self.details.grid(row=1,column=1,sticky='w',pady=(3,7))
        self.bar=ttk.Progressbar(b,maximum=100,style='File.Horizontal.TProgressbar')
        self.bar.grid(row=2,column=1,sticky='ew')
        self.status=shell.label(b,'',role='muted');self.status.grid(row=3,column=1,sticky='w',pady=(5,0))
        shell.button(b,'选择',lambda:shell.select(self.row['id']),controlled=False).grid(row=0,column=2,padx=(12,0))
        self.title.bind('<Button-1>',lambda e:shell.select(self.row['id']))
        self.update(row)

    def update(self,row):
        self.row=row
        self.title.configure(text=f"{row['author']}  /  {row['tweet_id']} · {row['media_id']}")
        self.details.configure(text=f"{row['quality']}  ·  MP4  ·  {row['bytes']/1048576:.1f} MB" if row['bytes'] else f"{row['quality']}  ·  MP4")
        labels={'pending':'等待下载','downloading':'正在下载 / 校验','paused':'已暂停','failed':'失败，可重试','completed':'有完成记录'}
        self.status.configure(text=labels[row['state']] + (' · '+row['error'] if row['error'] else ''))
        self.bar.configure(value=100 if row['state']=='completed' else 0)
        cached=self.shell.progress_cache.get(row['id'])
        if cached and row['state'] in ('downloading','paused'):self.progress(*cached)

    def progress(self,done,total,speed):
        self.bar.configure(value=min(100,100*done/total) if total else 0)
        self.status.configure(text=f'{done/1048576:.1f} / {total/1048576:.1f} MB · {speed/1048576:.2f} MB/s' if total else f'{done/1048576:.1f} MB · 总长度未知')


class Shell:
    def __init__(self,app,qualities):
        self.app, self.root, self.qualities=app,app.root,qualities
        self.palette=PALETTES[app.theme.get()]
        self.backgrounds=[];self.labels=[];self.buttons=[];self.cards=[];self.task_cards={}
        self.current_page='overview';self.rows={};self.nav={};self.task_page=0;self.speeds={};self.progress_cache={}
        self.root.title(f'X2LT {__version__} · 本地视频归档')
        self.root.geometry('1220x900');self.root.minsize(1040,800)
        self.style=ttk.Style(self.root);self.style.theme_use('clam')
        self.sidebar=self.frame(self.root,role='sidebar');self.sidebar.pack(side='left',fill='y',ipadx=12)
        self.label(self.sidebar,'XL   X2LT',role='accent',bg='sidebar',font=(FONT,20,'bold')).pack(anchor='w',padx=20,pady=(28,26))
        for key in ('overview','downloads','archive','organize'):
            self.nav[key]=self.button(self.sidebar,PAGES[key],lambda k=key:self.navigate(k),controlled=False)
            self.nav[key].pack(fill='x',padx=10,pady=5)
        self.label(self.sidebar,'本地归档',role='muted',bg='sidebar').pack(side='bottom',anchor='w',padx=22,pady=20)
        self.nav['settings']=self.button(self.sidebar,'设置',lambda:self.navigate('settings'),controlled=False)
        self.nav['settings'].pack(side='bottom',fill='x',padx=10,pady=10)
        self.main=self.frame(self.root);self.main.pack(side='left',fill='both',expand=True,padx=24,pady=22)
        header=self.frame(self.main);header.pack(fill='x',pady=(0,18))
        self.page_title=self.label(header,'总览',font=(FONT,23,'bold'));self.page_title.pack(side='left')
        self.button(header,'浅色 / 深色',lambda:app.set_theme('dark' if app.theme.get()=='light' else 'light'),controlled=False).pack(side='right')
        self.new_download_button=self.button(header,'＋ 新建下载',self.new_download,primary=True)
        self.new_download_button.pack(side='right',padx=12)
        app.pause_button=self.button(header,'暂停任务',app.pause,controlled=False)
        app.pause_button.pack(side='right');app.pause_button.configure(state='disabled')
        self.host=self.frame(self.main);self.host.pack(fill='both',expand=True)
        self.host.rowconfigure(0,weight=1);self.host.columnconfigure(0,weight=1)
        self.pages={k:self.frame(self.host) for k in PAGES}
        for page in self.pages.values():page.grid(row=0,column=0,sticky='nsew')
        self._overview();self._downloads();self._archive();self._organize();self._settings()
        progress=self.frame(self.main);progress.pack(fill='x',pady=(12,0));progress.columnconfigure(1,weight=1)
        app.total_text=tk.StringVar(value='总进度 · 就绪');app.file_text=tk.StringVar(value='当前文件 · 就绪')
        self.label(progress,variable=app.total_text,role='muted').grid(row=0,column=0,sticky='w',padx=(0,14))
        app.total_bar=ttk.Progressbar(progress,maximum=100);app.total_bar.grid(row=0,column=1,sticky='ew',pady=3)
        self.label(progress,variable=app.file_text,role='muted').grid(row=1,column=0,sticky='w',padx=(0,14))
        app.file_bar=ttk.Progressbar(progress,maximum=100,style='File.Horizontal.TProgressbar');app.file_bar.grid(row=1,column=1,sticky='ew',pady=3)
        self.label(self.main,variable=app.status,role='muted',wraplength=910).pack(anchor='w',pady=(8,0))
        self.apply_theme();self.navigate('overview')

    def frame(self,parent,role='bg'):
        w=tk.Frame(parent,bg=self.palette[role],bd=0);self.backgrounds.append((w,role));return w

    def label(self,parent,text='',role='text',bg=None,variable=None,**kw):
        background=bg or ('card' if parent in [c.body for c in self.cards] else 'bg')
        w=tk.Label(parent,text=text,textvariable=variable,bg=self.palette[background],fg=self.palette[role],anchor='w',font=kw.pop('font',(FONT,10)),**kw)
        self.labels.append((w,role,background));return w

    def button(self,parent,text,command,primary=False,controlled=True):
        w=tk.Button(parent,text=text,command=command,font=(FONT,10),bd=0,relief='flat',padx=14,pady=8,cursor='hand2',takefocus=True)
        self.buttons.append((w,primary))
        if controlled:self.app.controls.append(w)
        return w

    def _overview(self):
        page=self.pages['overview']
        self.label(page,'把值得保留的视频，好好收起来。',role='muted').pack(anchor='w',pady=(0,14))
        stats=self.frame(page);stats.pack(fill='x',pady=(0,12))
        self.stats={}
        for index,(key,title) in enumerate([('account','账号凭证'),('speed','下载速度'),('active','进行中'),('completed','归档总记录')]):
            stats.columnconfigure(index,weight=1,uniform='stats')
            card=Card(self,stats);card.grid(row=0,column=index,sticky='nsew',padx=(0,10 if index<3 else 0))
            self.label(card.body,title,role='muted').pack(anchor='w')
            self.stats[key]=tk.StringVar(value='—')
            self.label(card.body,variable=self.stats[key],font=(FONT,22,'bold')).pack(anchor='w',pady=(9,3))
        middle=self.frame(page);middle.pack(fill='x',pady=(0,12));middle.columnconfigure(0,weight=3);middle.columnconfigure(1,weight=2)
        card=Card(self,middle);card.grid(row=0,column=0,sticky='nsew',padx=(0,12))
        self.label(card.body,'当前任务',font=(FONT,12,'bold')).pack(anchor='w')
        self.active_title=tk.StringVar(value='还没有运行中的任务')
        self.label(card.body,variable=self.active_title,wraplength=470).pack(anchor='w',pady=(16,8))
        self.active_bar=ttk.Progressbar(card.body,maximum=100,style='File.Horizontal.TProgressbar');self.active_bar.pack(fill='x',pady=(0,10))
        self.active_detail=tk.StringVar(value='点击右上角“新建下载”开始')
        self.label(card.body,variable=self.active_detail,role='muted').pack(anchor='w')
        card=Card(self,middle);card.grid(row=0,column=1,sticky='nsew')
        self.label(card.body,'视频整理',font=(FONT,12,'bold')).pack(anchor='w')
        self.organize_count=tk.StringVar(value='0 个完成记录')
        self.label(card.body,variable=self.organize_count,font=(FONT,17,'bold')).pack(anchor='w',pady=(10,6))
        self.button(card.body,'整理视频 →',lambda:self.navigate('organize'),primary=True,controlled=False).pack(anchor='w')
        self.label(card.body,'JSON 保留原位，视频可集中整理',role='muted',wraplength=280).pack(anchor='w',pady=(6,0))
        card=Card(self,page);card.pack(fill='x')
        self.label(card.body,'最近完成',font=(FONT,12,'bold')).pack(anchor='w',pady=(0,8))
        self.recent_text=tk.StringVar(value='完成下载后，归档记录会显示在这里。')
        self.label(card.body,variable=self.recent_text,wraplength=850,justify='left').pack(anchor='w',pady=8)
        self.label(page,variable=self.app.summary,role='muted').pack(anchor='w',pady=14)

    def _downloads(self):
        page=self.pages['downloads'];line=self.frame(page);line.pack(fill='x',pady=(0,12))
        self.button(line,'继续未完成 / 重试',lambda:self.app.start('resume'),primary=True).pack(side='left')
        self.button(line,'强制重下选中项',lambda:self.app.start('force')).pack(side='left',padx=10)
        pager=self.frame(page);pager.pack(side='bottom',fill='x',pady=(8,0))
        self.task_page_text=tk.StringVar(value='第 1 页')
        self.button(pager,'上一页',lambda:self.turn_page(-1),controlled=False).pack(side='left')
        self.label(pager,variable=self.task_page_text,role='muted').pack(side='left',padx=12)
        self.button(pager,'下一页',lambda:self.turn_page(1),controlled=False).pack(side='left')
        self.task_area=ScrollArea(self,page);self.task_area.pack(fill='both',expand=True)
        self.empty_tasks=self.label(self.task_area.body,'暂无任务。点击“新建下载”添加推文链接或读取点赞。',role='muted')
        self.empty_tasks.pack(anchor='w',padx=12,pady=24)

    def _archive(self):
        page=self.pages['archive'];line=self.frame(page);line.pack(fill='x',pady=(0,12))
        self.button(line,'校验已完成文件',lambda:self.app.start('verify'),primary=True).pack(side='left')
        self.button(line,'强制重下选中项',lambda:self.app.start('force')).pack(side='left',padx=10)
        self.button(line,'打开保存目录',self.app.open_dir).pack(side='right')
        columns=('video','quality','state','progress','speed','detail')
        self.app.tree=ttk.Treeview(page,columns=columns,show='headings',selectmode='browse')
        for key,label,width in [('video','作者 / 推文',210),('quality','画质',65),('state','记录状态',95),('progress','大小 / 进度',120),('speed','速度',90),('detail','校验 / 说明',220)]:
            self.app.tree.heading(key,text=label);self.app.tree.column(key,width=width,minwidth=45,stretch=key=='detail')
        bar=ttk.Scrollbar(page,command=self.app.tree.yview);self.app.tree.configure(yscrollcommand=bar.set)
        bar.pack(side='right',fill='y');self.app.tree.pack(fill='both',expand=True)

    def _organize(self):
        page=self.pages['organize']
        self.label(page,'将视频集中存放，或恢复到原来的分层目录。',role='muted').pack(anchor='w',pady=(0,16))
        for title,description,actions in [
            ('集中视频','只移动已完成的 MP4，JSON 完成记录留在原目录。',[('一键脱壳',self.app.on_flatten),('选择集中目录…',lambda:self.app.on_flatten(custom=True))]),
            ('恢复目录','找不到原位置时，可选择视频现在所在的文件夹，按大小和哈希找回。',[('脱壳回退',self.app.on_rollback),('查找并回退…',lambda:self.app.on_rollback(search=True))])]:
            card=Card(self,page);card.pack(fill='x',pady=(0,16))
            self.label(card.body,title,font=(FONT,15,'bold')).pack(anchor='w')
            self.label(card.body,description,role='muted',wraplength=820).pack(anchor='w',pady=12)
            line=self.frame(card.body,role='card');line.pack(anchor='w')
            for index,(name,action) in enumerate(actions):self.button(line,name,action,primary=index==0).pack(side='left',padx=(0,10))
        self.label(page,'继续沿用原“保存目录”以保持归档范围；完成记录不代表视频仍在原位置。',role='muted',wraplength=850).pack(anchor='w')

    def _settings(self):
        area=ScrollArea(self,self.pages['settings']);area.pack(fill='both',expand=True)
        card=Card(self,area.body);card.pack(fill='x',pady=(0,14));b=card.body;b.columnconfigure(1,weight=1)
        self.label(b,'账号与网络',font=(FONT,14,'bold')).grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,12))
        for index,(name,var,secret) in enumerate([('X 用户名',self.app.username,False),('代理地址',self.app.proxy,True),('auth_token',self.app.auth,True),('ct0',self.app.ct0,True)],1):
            self.label(b,name).grid(row=index,column=0,sticky='w',padx=(0,15),pady=6)
            entry=ttk.Entry(b,textvariable=var,show='●' if secret else '');entry.grid(row=index,column=1,columnspan=2,sticky='ew',pady=6);self.app.controls.append(entry)
        self.button(b,'保存加密凭证',self.app.save,primary=True).grid(row=5,column=1,sticky='w',pady=12)
        self.button(b,'导入兼容配置',self.app.import_old).grid(row=5,column=2,sticky='e')
        self.label(b,'仅表示已填写凭证，不代表已验证登录。Cookie 由当前 Windows 用户加密。',role='muted',wraplength=800).grid(row=7,column=0,columnspan=3,sticky='w')
        card=Card(self,area.body);card.pack(fill='x',pady=(0,14));b=card.body;b.columnconfigure(1,weight=1)
        self.label(b,'保存与下载',font=(FONT,14,'bold')).grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,12))
        self.label(b,'保存目录').grid(row=1,column=0,sticky='w',padx=(0,15))
        entry=ttk.Entry(b,textvariable=self.app.output);entry.grid(row=1,column=1,sticky='ew');self.app.controls.append(entry)
        self.button(b,'选择目录',self.app.choose_dir).grid(row=1,column=2,padx=(10,0))
        for index,(title,var,values) in enumerate([('画质',self.app.quality,list(self.qualities)),('每文件连接数',self.app.connections,[1,2,4]),('单文件上限 MB',self.app.max_mb,None),('请求策略',self.app.request_strategy,list(REQUEST_STRATEGIES))],2):
            self.label(b,title).grid(row=index,column=0,sticky='w',pady=9)
            control=ttk.Combobox(b,textvariable=var,values=values,state='readonly',width=18) if values else ttk.Spinbox(b,from_=1,to=65536,textvariable=var,width=18)
            control.grid(row=index,column=1,sticky='w',pady=9);self.app.controls.append(control)
        self.button(b,'保存设置',self.app.save,primary=True).grid(row=6,column=1,sticky='w',pady=10)
        self.label(b,'同时下载最多 2 个视频；原始配置文件中的明文凭证不会被自动删除。',role='muted',wraplength=780).grid(row=7,column=0,columnspan=3,sticky='w')
        card=Card(self,area.body);card.pack(fill='x');self.label(card.body,'运行记录',font=(FONT,12,'bold')).pack(anchor='w',pady=(0,8))
        self.app.log=tk.Text(card.body,height=5,wrap='word',state='disabled',font=(FONT,9),bd=0,highlightthickness=0)
        self.app.log.pack(fill='x')

    def new_download(self):
        if self.app.worker and self.app.worker.is_alive():return
        if getattr(self,'dialog',None) is not None and self.dialog.winfo_exists():self.dialog.lift();return
        self.dialog=tk.Toplevel(self.root);self.dialog.title('新建下载 · X2LT');self.dialog.geometry('620x330');self.dialog.transient(self.root)
        b=self.frame(self.dialog);b.pack(fill='both',expand=True,padx=24,pady=22)
        self.label(b,'保存一个链接，或同步你的点赞',font=(FONT,16,'bold')).pack(anchor='w',pady=(0,18))
        self.label(b,'推文链接',role='muted').pack(anchor='w')
        entry=ttk.Entry(b,textvariable=self.app.links);entry.pack(fill='x',pady=(6,12));self.app.controls.append(entry)
        def start(mode):
            self.app.start(mode)
            if self.app.worker and self.app.worker.is_alive():self.dialog.destroy();self.navigate('downloads')
        self.button(b,'下载链接',lambda:start('link'),primary=True).pack(anchor='w')
        line=self.frame(b);line.pack(fill='x',pady=(24,0))
        self.button(line,'最新点赞视频',lambda:start('latest')).pack(side='left',padx=(0,10))
        self.button(line,'全部点赞视频',lambda:start('all')).pack(side='left')
        self.button(line,'账号设置',lambda:(self.dialog.destroy(),self.navigate('settings')),controlled=False).pack(side='right')
        self.apply_theme();entry.focus_set()

    def navigate(self,key):
        self.current_page=key;self.pages[key].tkraise();self.page_title.configure(text=PAGES[key]);self.apply_nav()

    def apply_nav(self):
        for key,w in self.nav.items():w.configure(bg=self.palette['selected'] if key==self.current_page else self.palette['sidebar'])

    def select(self,job_id):
        if self.app.tree.exists(str(job_id)):
            self.app.tree.selection_set(str(job_id));self.app.tree.see(str(job_id))
            self.app.status.set(f'已选中任务 {job_id}；可在下载任务或归档记录中强制重下')

    def refresh_stats(self,rows):
        self.stats['account'].set('已填写' if self.app.auth.get().strip() else '未配置')
        self.stats['active'].set(str(sum(r['state']=='downloading' for r in rows)))
        completed=[r for r in rows if r['state']=='completed']
        self.stats['completed'].set(f'{len(completed):,}')
        active_ids={r['id'] for r in rows if r['state']=='downloading'}
        self.speeds={key:value for key,value in self.speeds.items() if key in active_ids}
        self.stats['speed'].set(f'{sum(self.speeds.values())/1048576:.2f} MB/s')
        self.organize_count.set(f"{sum(r['state']=='completed' and Path(r['output']).resolve()==Path(self.app.output.get()).resolve() for r in rows)} 个完成记录")
        active=next((r for r in rows if r['state']=='downloading'),None)
        self.active_title.set(f"{active['author']} / {active['tweet_id']}" if active else '当前没有运行中的下载')
        if not active:self.active_bar.configure(value=0);self.active_detail.set('新建下载，或到下载任务继续未完成项')
        self.recent_text.set('\n\n'.join(f"MP4   {r['author']} / {r['tweet_id']}      {r['quality']}  ·  {r['bytes']/1048576:.1f} MB   ·   有完成记录" for r in completed[:3]) or '完成下载后，归档记录会显示在这里。')

    def refresh_summary(self):
        counts, output_count, active, recent = self.app.store.summary(self.app.output.get())
        self.counts = counts
        self.refresh_stats(active + recent)
        self.stats['active'].set(str(counts.get('downloading',0)))
        self.stats['completed'].set(f"{counts.get('completed',0):,}")
        self.organize_count.set(f'{output_count} 个完成记录')

    def render(self,rows,incremental=False):
        if not incremental:self.rows={}
        changed={r['id'] for r in rows}
        self.rows.update((r['id'],r) for r in rows)
        for row in rows:
            if row['state'] not in ('downloading','paused'):
                self.progress_cache.pop(row['id'],None)
        self.refresh_summary()
        count=sum(n for state,n in self.counts.items() if state!='completed')
        pages=max(1,(count+99)//100)
        self.task_page=min(self.task_page,pages-1)
        self.task_page_text.set(f'第 {self.task_page+1} / {pages} 页 · {count} 个未完成')
        unfinished=self.app.store.unfinished_page(self.task_page*100)
        visible={r['id'] for r in unfinished}
        for key in list(self.task_cards):
            if key not in visible:self.task_cards.pop(key).card.destroy()
        if unfinished:self.empty_tasks.pack_forget()
        else:self.empty_tasks.pack(anchor='w',padx=12,pady=24)
        added=False
        for row in unfinished:
            if row['id'] in self.task_cards:
                if not incremental or row['id'] in changed:self.task_cards[row['id']].update(row)
            else:
                self.task_cards[row['id']]=TaskCard(self,self.task_area.body,row)
                added=True
        if added:self.apply_theme()

    def turn_page(self,delta):
        count=sum(n for state,n in self.counts.items() if state!='completed')
        self.task_page=max(0,min(self.task_page+delta,max(0,(count-1)//100)))
        self.render([],incremental=True)
        self.task_area.canvas.yview_moveto(0)

    def progress(self,job_id,done,total,speed):
        self.progress_cache[job_id]=(done,total,speed)
        if job_id in self.task_cards:self.task_cards[job_id].progress(done,total,speed)
        self.speeds[job_id]=speed
        self.stats['speed'].set(f'{sum(self.speeds.values())/1048576:.2f} MB/s')
        row=self.rows.get(job_id)
        if row:self.active_title.set(f"{row['author']} / {row['tweet_id']}")
        self.active_bar.configure(value=min(100,100*done/total) if total else 0)
        self.active_detail.set(f'{done/1048576:.1f} MB · {speed/1048576:.2f} MB/s')

    def apply_theme(self):
        self.palette=PALETTES[self.app.theme.get()];p=self.palette;self.root.configure(bg=p['bg'])
        self.backgrounds[:]=[(w,role) for w,role in self.backgrounds if w.winfo_exists()]
        self.labels[:]=[(w,role,bg) for w,role,bg in self.labels if w.winfo_exists()]
        self.buttons[:]=[(w,primary) for w,primary in self.buttons if w.winfo_exists()]
        for w,role in self.backgrounds:
            if w.winfo_exists():w.configure(bg=p[role])
        for w,role,bg in self.labels:
            if w.winfo_exists():w.configure(bg=p[bg],fg=p[role])
        for w,primary in self.buttons:
            if w.winfo_exists():w.configure(bg=p['accent'] if primary else p['entry'],fg='#ffffff' if primary else p['text'],activebackground=p['selected'],activeforeground=p['text'],disabledforeground=p['muted'])
        self.cards[:]=[c for c in self.cards if c.winfo_exists()]
        for c in self.cards:c.draw()
        self.style.configure('.',font=(FONT,10),background=p['card'],foreground=p['text'],bordercolor=p['line'],troughcolor=p['entry'],fieldbackground=p['entry'])
        self.style.configure('TEntry',fieldbackground=p['entry'],foreground=p['text'],insertcolor=p['text'])
        self.style.configure('TCombobox',fieldbackground=p['entry'],foreground=p['text'],arrowcolor=p['text'])
        self.style.map('TCombobox',fieldbackground=[('readonly',p['entry'])],foreground=[('readonly',p['text'])])
        self.style.configure('Treeview',background=p['card'],fieldbackground=p['card'],foreground=p['text'],rowheight=34,borderwidth=0)
        self.style.configure('Treeview.Heading',background=p['entry'],foreground=p['muted'],padding=8)
        self.style.map('Treeview',background=[('selected',p['selected'])],foreground=[('selected',p['text'])])
        self.style.configure('Horizontal.TProgressbar',background=p['accent'],troughcolor=p['line'],borderwidth=0,lightcolor=p['accent'],darkcolor=p['accent'])
        self.style.configure('File.Horizontal.TProgressbar',background=p['cyan'],troughcolor=p['line'],lightcolor=p['cyan'],darkcolor=p['cyan'])
        self.app.log.configure(bg=p['card'],fg=p['muted'],insertbackground=p['text'])
        self.apply_nav()
