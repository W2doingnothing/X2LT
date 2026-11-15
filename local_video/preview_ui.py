# SPDX-License-Identifier: GPL-2.0-only
"""Main-thread thumbnail subscriptions and media detail windows."""
import itertools
import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
import weakref
import webbrowser

from PIL import ImageTk

from .policy import validate_source_url
from .previews import PreviewService
from .runner import destination_for


class PreviewUI:
    def __init__(self, app):
        self.app = app
        self.service = PreviewService(app.data_dir / 'previews', app.events.put)
        self.labels = weakref.WeakKeyDictionary()
        self.tokens = {}
        self.counter = itertools.count()
        self.detail = None
        self.detail_job = None

    def bind(self, label, row, size=(144, 96)):
        signature = (row['id'], row.get('preview_url'), row.get('local_path'), row.get('state'), size)
        old = self.labels.get(label)
        if old and old['signature'] == signature:
            return
        if old:
            self.tokens.pop(old['token'], None)
        token = next(self.counter)
        self.labels[label] = {'token': token, 'signature': signature, 'row': dict(row), 'size': size, 'requested': False}
        self.tokens[token] = weakref.ref(label)
        label.image = None
        text='暂无预览' if not row.get('preview_url') and row.get('media_type') != 'image' else '等待预览'
        if hasattr(label,'preview_placeholder'):label.preview_placeholder(text)
        else:label.configure(image='',text=text)

    def unbind(self, label):
        sub=self.labels.pop(label,None)
        if sub:self.tokens.pop(sub['token'],None)
        label.image=None

    def visible(self, label):
        if not label.winfo_viewable():
            return False
        x, y = label.winfo_rootx(), label.winfo_rooty()
        right, bottom = x + label.winfo_width(), y + label.winfo_height()
        parent = label.master
        while parent is not None:
            shell = self.app.shell
            for key, page in shell.pages.items():
                if parent is page and key != shell.current_page:
                    return False
            if isinstance(parent, (tk.Canvas, tk.Tk, tk.Toplevel)):
                px, py = parent.winfo_rootx(), parent.winfo_rooty()
                if right <= px or bottom <= py or x >= px + parent.winfo_width() or y >= py + parent.winfo_height():
                    return False
            if isinstance(parent, (tk.Toplevel, tk.Tk)):
                break
            parent = parent.master
        return True

    def refresh_visible(self):
        for label, sub in list(self.labels.items()):
            if not label.winfo_exists():
                self.tokens.pop(sub['token'], None)
                self.labels.pop(label, None)
                label.image = None
                continue
            if sub['requested'] or not self.visible(label):
                continue
            row = sub['row']
            if not row.get('preview_url') and not (row.get('media_type') == 'image' and row.get('state') == 'completed'):
                continue
            future = self.service.request(row, self.app.proxy.get(), sub['token'], sub['size'])
            if future is not None:
                sub['requested'] = True
        # Remove weak subscriptions whose widgets were garbage collected.
        for token, ref in list(self.tokens.items()):
            if ref() is None:
                self.tokens.pop(token, None)

    def receive(self, event):
        image = event.get('image')
        try:
            ref = self.tokens.get(event['token'])
            label = ref() if ref else None
            if label is None or not label.winfo_exists():
                return
            if image is None:
                if hasattr(label,'preview_placeholder'):label.preview_placeholder('预览暂不可用')
                else:label.configure(text='预览暂不可用')
                return
            if hasattr(label,'set_preview'):
                label.set_preview(image)
                return
            photo = ImageTk.PhotoImage(image, master=self.app.root)
            label.configure(image=photo, text='')
            label.image = photo
        finally:
            if image is not None:
                image.close()

    def show_detail(self, job_id):
        rows = self.app.store.get_many([job_id])
        if not rows:
            return
        row = rows[0]
        self.detail_rows=sorted(self.app.store.tweet_rows(row['tweet_id']),key=lambda r:(int(r['media_id']) if r['media_id'].isdigit() else 10**12,r['id']))
        position=next(i for i,r in enumerate(self.detail_rows) if r['id']==job_id)
        if self.detail is not None and self.detail.winfo_exists():
            self.detail.destroy()
        self.detail_job = job_id
        shell = self.app.shell
        self.detail = tk.Toplevel(self.app.root)
        self.detail.title(f"推文详情 · {row['author']} / {row['tweet_id']}")
        self.detail.geometry('820x760')
        self.detail.minsize(800, 650)
        self.detail.transient(self.app.root)
        body = shell.frame(self.detail)
        body.pack(fill='both', expand=True, padx=22, pady=18)
        shell.label(body, f"{row['author']} / {row['tweet_id']} · 媒体 {row['media_id']}", font=('Microsoft YaHei UI', 14, 'bold')).pack(anchor='w')
        nav=shell.frame(body);nav.pack(fill='x',pady=(10,0))
        previous=shell.button(nav,'‹',lambda:self.choose_version(position-1),controlled=False);previous.pack(side='left')
        previous.configure(state='normal' if position else 'disabled')
        choices=[f"媒体 {r['media_id']} · {'原图' if r.get('media_type')=='image' else r['quality']} · {Path(r['output']).name}" for r in self.detail_rows]
        picker=ttk.Combobox(nav,values=choices,state='readonly',width=55);picker.pack(side='left',fill='x',expand=True,padx=8);picker.current(position)
        picker.bind('<<ComboboxSelected>>',lambda e:self.choose_version(picker.current()))
        following=shell.button(nav,'›',lambda:self.choose_version(position+1),controlled=False);following.pack(side='right')
        following.configure(state='normal' if position+1<len(self.detail_rows) else 'disabled')
        self.detail_image = shell.label(body, '暂无预览')
        self.detail_image.pack(fill='x', pady=12)
        self.bind(self.detail_image, row, (720, 360))
        self.detail_text = tk.Text(body, wrap='word', height=8, bd=0, font=('Microsoft YaHei UI', 11))
        self.detail_text.insert('1.0', row.get('tweet_text') or '暂无正文，可点击“补全预览”重新解析此推文。')
        self.detail_text.configure(state='disabled')
        self.detail_text.pack(fill='both', expand=True, pady=8)
        line = shell.frame(body)
        line.pack(fill='x', pady=10)
        shell.button(line, '打开已下载文件', lambda: self.open_file(job_id), controlled=False).pack(side='left')
        shell.button(line, '打开推文', lambda: webbrowser.open(validate_source_url(row['source'])), controlled=False).pack(side='left', padx=8)
        shell.button(line,'强制重下此媒体',lambda:self.app.force_media(job_id)).pack(side='left',padx=8)
        shell.button(line, '补全预览', lambda: self.app.enrich(job_id)).pack(side='right')
        shell.apply_theme()
        self.style()

    def choose_version(self,index):
        if 0<=index<len(self.detail_rows):
            self.show_detail(self.detail_rows[index]['id'])

    def style(self):
        if self.detail is not None and self.detail.winfo_exists():
            p = self.app.shell.palette
            self.detail.configure(bg=p['bg'])
            self.detail_text.configure(bg=p['bg'], fg=p['text'], insertbackground=p['text'])

    def open_file(self, job_id):
        try:
            rows = self.app.store.get_many([job_id])
            if not rows or rows[0]['state'] != 'completed':
                raise ValueError('文件尚未下载完成')
            path = destination_for(rows[0])
            if not path.is_file():
                raise ValueError('文件已移动或不存在；可先查找并回退')
            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror('无法打开文件', self.app._error_text(exc), parent=self.app.root)

    def clear(self):
        try:
            self.service.clear()
            self.app.status.set('预览磁盘缓存已清空；正在显示的缩略图保留至离开页面')
        except Exception as exc:
            messagebox.showerror('无法清空缓存',self.app._error_text(exc),parent=self.app.root)

    def close(self):
        self.service.close()
        for label in list(self.labels):
            label.image = None
        self.labels.clear()
        self.tokens.clear()
