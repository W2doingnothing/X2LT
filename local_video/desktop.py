# SPDX-License-Identifier: GPL-2.0-only
"""Windows WebView2 host for the local React surface."""
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit,urlencode

from .desktop_service import DesktopService
from .runner import InstanceLock
from .security import private_directory


def allowed_navigation(url,expected):
    actual=urlsplit(str(url));target=urlsplit(str(expected))
    return actual.scheme=='http' and actual.hostname in ('127.0.0.1','localhost') and actual.netloc==target.netloc and actual.path=='/index.html' and actual.query==target.query


def bind_bridge(window,service):
    # Never set js_api to a service/proxy object: pywebview resolves private dotted
    # attributes even when they are omitted from JavaScript API enumeration.
    window.expose(service.call)


def successful_completion(callback):
    # Pinned pywebview reinjects its API even for cancelled WebView2 navigations.
    # The retained document must keep its existing pending callback dictionary.
    def completed(sender,args):
        if args.IsSuccess:callback(sender,args)
    return completed


def main(data_dir=None,smoke=False):
    import webview
    home=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent.parent
    data=private_directory(data_dir or Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'X2TLocalVideo')
    lock=InstanceLock(data/'instance.lock')
    service=None
    try:
        index=Path(__file__).resolve().parent/'web/index.html'
        if not index.is_file():raise RuntimeError('界面资源缺失，请先在 frontend 目录运行 npm ci 和 npm run build')
        if smoke:
            home=data/'synthetic-home';home.mkdir(exist_ok=True)
            (data/'desktop-smoke.json').unlink(missing_ok=True)
        service=DesktopService(data,home)
        if smoke:
            import hashlib
            from PIL import Image
            from .discovery import Video
            from .previews import thumbnail_url
            preview='https://pbs.twimg.com/media/x2lt_synthetic.jpg'
            key=hashlib.sha256(thumbnail_url(preview).encode()).hexdigest()
            sample=Image.new('RGB',(320,180),'#397863')
            sample.save(data/'previews'/(key+'.jpg'));sample.close()
            avatar='https://pbs.twimg.com/profile_images/999/synthetic_normal.jpg'
            avatar_key=hashlib.sha256(('avatar:'+avatar).encode()).hexdigest()
            sample=Image.new('RGB',(128,128),'#397863');sample.save(data/'previews'/(avatar_key+'.jpg'));sample.close()
            for i in range(4):
                service._store.enqueue(Video('123',str(i),'synthetic','https://video.twimg.com/synthetic.mp4','https://x.com/synthetic/status/123',tweet_text='桌面启动合成测试',preview_url=preview,author_id='999',display_name='合成作者',avatar_url=avatar),service._libraries.get()['path'],'best')
        appearance=service._settings.get('theme','light')
        accent=service._settings.get('accent','#397863')
        import re
        if appearance not in ('light','dark'):appearance='light'
        if not isinstance(accent,str) or not re.fullmatch('#[0-9a-fA-F]{6}',accent):accent='#397863'
        startup_query=urlencode({'desktop':'1','appearance':appearance,'accent':accent})
        window=webview.create_window('X2LT · 本地媒体归档',url=str(index)+'?'+startup_query,width=1220,height=900,min_size=(900,680),hidden=smoke,background_color='#0b120a' if appearance=='dark' else '#f2f9f2')
        bind_bridge(window,service)
        service._window=window
        window.events.closing+=lambda:service._close()
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=True
        webview.settings['ALLOW_DOWNLOADS']=False
        blocked_navigation=[]
        def install_navigation_guard():
            def guard(sender,args):
                if not allowed_navigation(str(args.Uri),window.real_url):
                    args.Cancel=True;blocked_navigation.append(str(args.Uri))
            browser=window.native.browser
            browser.webview.NavigationStarting+=guard
            browser.webview.NavigationCompleted-=browser.on_navigation_completed
            browser.webview.NavigationCompleted+=successful_completion(browser.on_navigation_completed)
        window.events.before_show+=install_navigation_guard
        smoke_started=threading.Event()
        def loaded():
            service._trusted_origin=urlsplit(window.get_current_url()).netloc
            if not smoke:return
            if smoke_started.is_set():return
            smoke_started.set()
            try:
                # Do not assume evaluate_js awaits promises; query the resolved marker.
                window.evaluate_js("""(async()=>{try {
                  while(!window.pywebview?.api?.call)await new Promise(r=>setTimeout(r,50));
                  const saved=await window.pywebview.api.call('settings',{accent:'#4f6bd9',theme:'dark'});
                  const state=await window.pywebview.api.call('snapshot',{});
                  const report={saved:saved.ok,version:state.data?.version,libraries:state.data?.libraries?.length,secretLeak:JSON.stringify(state).includes('auth_token'),accent:state.data?.settings?.accent};
                  const id=state.data.libraries[0].id;
                  const tagged=await window.pywebview.api.call('tags_set',{id:'123',tags:['桌面测试']});
                  const scoped=await window.pywebview.api.call('snapshot',{library_id:id,tags:['桌面测试']});
                  report.library_tags=tagged.ok&&scoped.ok&&scoped.data.total===1&&scoped.data.tasks[0].media.length===4;
                  const cover=await window.pywebview.api.call('library_cover',{id,kind:'emoji',value:'📚'});
                  const updated=await window.pywebview.api.call('snapshot',{});
                  report.cover_emoji=cover.ok&&updated.data.libraries[0].cover?.value==='📚';
                  const avatar=await window.pywebview.api.call('avatar',{id:'123'});
                  report.avatar_bridge=avatar.ok&&avatar.data?.startsWith('data:image/jpeg;base64,');
                  const privacy=await window.pywebview.api.call('settings',{privacy_mode:true});
                  const privateState=await window.pywebview.api.call('snapshot',{});
                  report.privacy_setting=privacy.ok&&privateState.data.settings.privacy_mode===true;
                  window.__x2ltSmoke=report;
                } catch(e){window.__x2ltSmoke={error:String(e)}}})();""")
                deadline=time.monotonic()+20;report=None
                while time.monotonic()<deadline:
                    report=window.evaluate_js('window.__x2ltSmoke || null')
                    if report and window.evaluate_js("!!document.querySelector('.app')"):break
                    time.sleep(.1)
                if not report or not report.get('saved') or report.get('secretLeak') or report.get('accent')!='#4f6bd9':raise RuntimeError(f'Bridge smoke failed: {report}')
                report['react_rendered']=bool(window.evaluate_js("document.querySelector('h1')?.textContent==='推特下载'"))
                # A hidden native WebView can suspend IntersectionObserver. Exercise
                # the same preview bridge directly and decode it with the web engine.
                window.evaluate_js("""(async()=>{const result=await window.pywebview.api.call('preview',{job_id:1});
                  if(result.ok&&result.data){const image=new Image();image.onload=()=>{window.__previewSmoke=image.naturalWidth===320};image.src=result.data;}
                })();""")
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    if window.evaluate_js('!!window.__previewSmoke'):break
                    time.sleep(.1)
                report['grouped_card']=window.evaluate_js("document.querySelectorAll('.tweet-card').length===1 && document.querySelectorAll('.media-tile').length===3")
                report['real_preview_bridge']=bool(window.evaluate_js('!!window.__previewSmoke'))
                if not report['react_rendered']:report['body']=window.evaluate_js('document.body.innerText')
                report['success']=report['react_rendered']
                window.evaluate_js("location.href='http://127.0.0.1:18753/blocked-smoke'")
                deadline=time.monotonic()+3
                while time.monotonic()<deadline and not blocked_navigation:time.sleep(.05)
                report['navigation_blocked']=bool(blocked_navigation) and allowed_navigation(window.evaluate_js('location.href'),window.real_url)
                report['restricted_bridge']=window._js_api is None and set(window._functions)=={'call'}
                report['success']=report['success'] and report['navigation_blocked'] and report['restricted_bridge'] and report['grouped_card'] and report['real_preview_bridge'] and report.get('library_tags') and report.get('cover_emoji') and report.get('avatar_bridge') and report.get('privacy_setting')
                (data/'desktop-smoke.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf8')
            except Exception as exc:(data/'desktop-smoke.json').write_text(json.dumps({'success':False,'error':str(exc)},ensure_ascii=False),encoding='utf8')
            finally:
                # Stop new React polling while outstanding native callbacks drain.
                window.evaluate_js('window.pywebview.api.call=()=>new Promise(()=>{})')
                time.sleep(.25)
                window.destroy()
        window.events.loaded+=loaded
        webview.start(gui='edgechromium',debug=False,private_mode=True,storage_path=str(data/'webview'),http_server=True)
        if smoke:
            report=data/'desktop-smoke.json'
            return 0 if report.exists() and json.loads(report.read_text(encoding='utf8')).get('success') else 1
        return 0
    finally:
        if service:service._close()
        lock.close()
