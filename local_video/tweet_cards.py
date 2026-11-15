# SPDX-License-Identifier: GPL-2.0-only
"""Tweet-level cards backed by independent media jobs."""
import tkinter as tk
from tkinter import ttk
import webbrowser

from PIL import Image, ImageTk

from .policy import profile_url
from .ui import Card, FONT

STATES={'pending':'等待','downloading':'下载中','paused':'已暂停','failed':'失败','completed':'已完成'}


def media_groups(rows):
    groups={}
    for row in rows:
        groups.setdefault((row.get('media_type','video'),row['media_id']),[]).append(row)
    return sorted(groups.values(),key=lambda group:(int(group[0]['media_id']) if group[0]['media_id'].isdigit() else 10**12,group[0]['media_id']))


def representative(rows):
    return next((r for r in rows if r['state']=='downloading'),next((r for r in rows if r['state']!='completed'),rows[0]))


class MediaTile(tk.Canvas):
    def __init__(self, owner, parent, versions):
        self.owner,self.shell=owner,owner.shell
        self.versions=versions;self.row=representative(versions)
        self.revision=0;self.signature=None;self.original=None;self.image=None;self.message='暂无预览';self.angle=90
        self.saved=owner.shell.progress_cache.get(self.row['id'])
        super().__init__(parent,width=180,height=140,highlightthickness=0,bd=0,cursor='hand2')
        self.bind('<Configure>',lambda e:self.draw())
        self.bind('<ButtonPress-1>',self.press)
        self.bind('<ButtonRelease-1>',self.release)
        self.bind('<Shift-MouseWheel>',self.horizontal_wheel)
        self.bind('<MouseWheel>',self.horizontal_wheel)
        self.drag_x=0
        self.shell.app.preview.bind(self,self.row,(220,136))

    def press(self,event):self.drag_x=event.x_root

    def release(self,event):
        distance=event.x_root-self.drag_x
        if abs(distance)>30:self.owner.shift(-1 if distance>0 else 1)
        else:self.shell.show_detail(self.row['id'])

    def horizontal_wheel(self,event):
        if event.state & 1:
            self.owner.shift(-1 if event.delta>0 else 1)
            return 'break'

    def preview_placeholder(self,text):
        self.message=text;self.draw()

    def set_preview(self,image):
        if self.original is not None:self.original.close()
        self.original=image.copy();self.revision+=1;self.draw()

    def update(self,versions):
        previous=self.row['id'];self.versions=versions;self.row=representative(versions)
        self.saved=self.shell.progress_cache.get(self.row['id'])
        if previous!=self.row['id'] and self.original is not None:
            self.original.close();self.original=None
        self.shell.app.preview.bind(self,self.row,(220,136));self.draw()

    def progress(self,job_id,done,total,speed):
        if self.row['id']==job_id:
            self.saved=(done,total,speed);self.draw()

    def tick(self):
        if self.row['state']=='downloading' and (not self.saved or not self.saved[1]):
            self.angle=(self.angle+24)%360
            self.itemconfigure(self.arc,start=self.angle)

    def draw(self):
        p=self.shell.palette;w=max(100,self.winfo_width());h=116
        signature=(self.shell.app.theme.get(),w,self.row['id'],self.row['state'],self.saved,self.revision,self.message,len(self.versions))
        if signature==self.signature:return
        self.signature=signature
        self.configure(bg=p['card']);self.delete('all');self.image=None
        self.create_rectangle(0,0,w,h,fill=p['entry'],outline='')
        unfinished=self.row['state']!='completed'
        if self.original is not None:
            raster=self.original.copy();raster.thumbnail((w,h))
            if unfinished:
                gray=Image.new('RGB',raster.size,'#777777')
                shaded=Image.blend(raster.convert('RGB'),gray,.57)
                raster.close();gray.close();raster=shaded
            self.image=ImageTk.PhotoImage(raster,master=self)
            raster.close();self.create_image(w/2,h/2,image=self.image)
        else:
            self.create_text(w/2,18,text=self.message,fill=p['muted'],font=(FONT,9),width=w-12)
        self.arc=self.create_arc(w/2-25,h/2-25,w/2+25,h/2+25,start=90,extent=0,style='arc',outline=p['accent'],width=4)
        self.progress_text=self.create_text(w/2,h/2,text='',fill=p['text'],font=(FONT,10,'bold'))
        if unfinished:
            self.create_oval(w/2-26,h/2-26,w/2+26,h/2+26,fill=p['card'],outline=p['line'],width=3)
            self.tag_raise(self.arc);self.tag_raise(self.progress_text)
            done,total,_=self.saved if self.saved else (0,None,0)
            text=f'{min(100,100*done/total):.0f}%' if total else STATES[self.row['state']]
            self.itemconfigure(self.progress_text,text=text)
            extent=-min(359.9,360*done/total) if total else -85 if self.row['state']=='downloading' else 0
            self.itemconfigure(self.arc,extent=extent,start=90 if total else self.angle)
        else:
            self.create_rectangle(w-50,5,w-5,25,fill=p['card'],outline='')
            self.create_text(w-27,15,text='✓ 完成',fill=p['accent'],font=(FONT,9))
        kind='图片' if self.row.get('media_type')=='image' else '视频'
        suffix=f' · {len(self.versions)} 版' if len(self.versions)>1 else ''
        self.create_text(w/2,129,text=f"{kind} {self.row['media_id']}{suffix} · {STATES[self.row['state']]}",fill=p['muted'],font=(FONT,9),width=w-8)

    def destroy(self):
        self.shell.app.preview.unbind(self)
        if self.original is not None:self.original.close();self.original=None
        super().destroy()


class TweetCard:
    def __init__(self,shell,parent,rows):
        self.shell=shell;self.rows=rows;self.tweet_id=rows[0]['tweet_id'];self.offset=0;self.tiles=[]
        self.card=Card(shell,parent);self.card.pack(fill='x',pady=(0,12))
        b=self.card.body;b.columnconfigure(1,weight=1)
        self.selected=tk.BooleanVar(value=self.tweet_id in shell.selected_tweets)
        check=ttk.Checkbutton(b,variable=self.selected,command=lambda:shell.toggle_tweet(self.tweet_id,self.selected.get()))
        check.grid(row=0,column=0,padx=(0,10),sticky='n')
        self.title=shell.label(b,'',font=(FONT,12,'bold'),wraplength=600)
        self.title.grid(row=0,column=1,sticky='w')
        self.profile=shell.label(b,'',role='accent',cursor='hand2')
        self.profile.grid(row=1,column=1,sticky='w',pady=(4,12));self.profile.bind('<Button-1>',lambda e:self.open_profile())
        self.strip=shell.frame(b,role='card');self.strip.grid(row=2,column=0,columnspan=2,sticky='ew',padx=(0,12))
        for i in range(3):self.strip.columnconfigure(i,weight=1,uniform='media')
        actions=shell.frame(b,role='card');actions.grid(row=0,column=2,rowspan=3,sticky='ne',padx=(10,0))
        self.ignore_button=shell.button(actions,'忽略',self.ignore,controlled=False);self.ignore_button.pack(fill='x',pady=(0,10))
        shell.button(actions,'详情',lambda:self.shell.show_detail(self.rows[0]['id']),controlled=False).pack(fill='x')
        pager=shell.frame(b,role='card');pager.grid(row=3,column=0,columnspan=3,sticky='ew',pady=(8,0))
        self.previous=shell.button(pager,'‹',lambda:self.shift(-1),controlled=False);self.previous.pack(side='left')
        self.position=shell.label(pager,'',role='muted',bg='card');self.position.pack(side='left',padx=10)
        self.next=shell.button(pager,'›',lambda:self.shift(1),controlled=False);self.next.pack(side='left')
        self.status=shell.label(pager,'',role='muted',bg='card');self.status.pack(side='right')
        b.bind('<Configure>',self.resize,add='+');self.update(rows)

    def resize(self,event):
        self.title.configure(wraplength=max(160,event.width-150))

    def open_profile(self):
        try:webbrowser.open(profile_url(self.rows[0]['author']))
        except ValueError as exc:self.shell.app.status.set(str(exc))

    def ignore(self):self.shell.app.ignore_tweets([self.tweet_id],not self.shell.app.store.is_ignored(self.tweet_id))

    def selected_changed(self):
        self.selected.set(self.tweet_id in self.shell.selected_tweets)
        self.card.selected=self.selected.get();self.card.draw()

    def update(self,rows):
        self.rows=rows;self.groups=media_groups(rows)
        text=next((r.get('tweet_text') for r in rows if r.get('tweet_text')),'')
        title=next((line.strip() for line in text.splitlines() if line.strip()),f'推文 {self.tweet_id}')
        self.title.configure(text=title[:100]+('…' if len(title)>100 else ''))
        self.profile.configure(text='@'+rows[0]['author'])
        self.ignore_button.configure(text='取消忽略' if self.shell.app.store.is_ignored(self.tweet_id) else '忽略')
        completed=sum(all(r['state']=='completed' for r in group) for group in self.groups)
        self.status.configure(text=f'{completed} / {len(self.groups)} 个媒体完成')
        self.offset=min(self.offset,max(0,len(self.groups)-3))
        shown=self.groups[self.offset:self.offset+3]
        if len(self.tiles)==len(shown) and all(t.row['media_id']==g[0]['media_id'] and t.row.get('media_type')==g[0].get('media_type') for t,g in zip(self.tiles,shown)):
            for tile,group in zip(self.tiles,shown):tile.update(group)
        else:self.render_tiles()
        self.update_position();self.selected_changed()

    def render_tiles(self):
        for tile in self.tiles:tile.destroy()
        self.tiles=[]
        for i,group in enumerate(self.groups[self.offset:self.offset+3]):
            tile=MediaTile(self,self.strip,group);tile.grid(row=0,column=i,sticky='ew',padx=(0,10 if i<2 else 0));self.tiles.append(tile)

    def update_position(self):
        self.position.configure(text=f'{self.offset+1}–{min(self.offset+3,len(self.groups))} / {len(self.groups)}')
        self.previous.configure(state='normal' if self.offset else 'disabled')
        self.next.configure(state='normal' if self.offset+3<len(self.groups) else 'disabled')

    def shift(self,delta):
        target=max(0,min(self.offset+delta,max(0,len(self.groups)-3)))
        if target==self.offset:return
        self.offset=target;self.render_tiles();self.update_position()

    def progress(self,job_id,done,total,speed):
        for tile in self.tiles:tile.progress(job_id,done,total,speed)

    def destroy(self):
        for tile in self.tiles:tile.destroy()
        self.card.destroy()
