# SPDX-License-Identifier: GPL-2.0-only
"""Fetch integrity-checked npm runtime sources and preserve their license files."""
import base64
import hashlib
from importlib.metadata import distribution
import json
from pathlib import Path
import re
import tarfile
import urllib.request
import zipfile
import sys
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from local_video import __version__


def main():
    lock=json.loads((ROOT/'frontend/package-lock.json').read_text())
    cache=ROOT/'.release-work/npm-sources';cache.mkdir(parents=True,exist_ok=True)
    licenses=ROOT/'third-party-licenses/frontend';licenses.mkdir(exist_ok=True)
    entries=[]
    for location,pkg in lock['packages'].items():
        if not location or 'resolved' not in pkg:continue
        name=location.split('node_modules/')[-1]
        if pkg.get('dev') and name not in ('tailwindcss','vite'):continue
        filename=re.sub('[^A-Za-z0-9_.-]','-',name)+'-'+pkg['version']+'.tgz'
        algorithm,expected=pkg['integrity'].split('-',1)
        if algorithm not in ('sha512','sha256'):raise ValueError('Unsupported npm integrity')
        target=cache/filename
        if not target.exists():
            with urllib.request.urlopen(pkg['resolved'],timeout=60) as response:target.write_bytes(response.read())
        data=target.read_bytes()
        if base64.b64encode(hashlib.new(algorithm,data).digest()).decode()!=expected:raise ValueError('npm source integrity failed: '+name)
        entries.append({'name':name,'version':pkg['version'],'license':pkg.get('license','See archive'),'url':pkg['resolved'],'file':filename,'sha256':hashlib.sha256(data).hexdigest()})
        with tarfile.open(target) as archive:
            for member in archive.getmembers():
                if member.isfile() and re.search(r'(?:^|/)(?:license|licence|copying|notice)(?:\.[^/]*)?$',member.name,re.I):
                    content=archive.extractfile(member).read()
                    (licenses/(filename[:-4]+'-'+member.name.replace('/','_')+'.txt')).write_bytes(content)
    (cache/'NPM-SOURCES.json').write_text(json.dumps(entries,indent=2),encoding='utf8')
    output=ROOT/'.release'/f'StashRat-{__version__}-web-sources.zip'
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.write(cache/'NPM-SOURCES.json','NPM-SOURCES.json')
        archive.write(ROOT/'frontend/package-lock.json','package-lock.json')
        for entry in entries:archive.write(cache/entry['file'],entry['file'])
    # Installed wheel license texts, preserving all notices in each license directory.
    for name in ('pywebview','pythonnet','clr_loader','cffi','proxy_tools','bottle','typing_extensions','pycparser',
                 'telethon','python-socks','pyaes','rsa','pyasn1','qrcode','colorama'):
        dist=distribution(name);found=False
        for file in dist.files:
            if 'licenses/' in str(file).lower() or re.search(r'(?:^|/)(?:license|copying|notice)(?:\.[^/]*)?$',str(file),re.I):
                path=dist.locate_file(file)
                if path.is_file():(ROOT/'third-party-licenses'/f'{name}-{path.name}.txt').write_bytes(path.read_bytes());found=True
        if not found:
            sources=json.loads((ROOT/'.release-work/third-party-sources/SOURCES.json').read_text())
            source=next(s for s in sources if s['name']==name)
            with tarfile.open(ROOT/'.release-work/third-party-sources'/source['file']) as archive:
                for member in archive.getmembers():
                    if member.isfile() and re.search(r'/(?:license|copying)(?:\.[^/]*)?$',member.name,re.I):
                        (ROOT/'third-party-licenses'/f'{name}-{Path(member.name).name}.txt').write_bytes(archive.extractfile(member).read());found=True
            if not found and name=='proxy_tools':
                import proxy_tools
                header=Path(proxy_tools.__file__).read_text(encoding='utf8').split('import sys')[0]
                (ROOT/'third-party-licenses/proxy_tools-UPSTREAM-NOTICE.txt').write_text('proxy_tools 0.1.0: package metadata declares MIT; source header declares BSD and credits Werkzeug. The distribution omits its referenced LICENSE file. Original source header follows; corresponding Werkzeug BSD license is supplied separately.\n\n'+header,encoding='utf8')
                with urllib.request.urlopen('https://raw.githubusercontent.com/pallets/werkzeug/0.9.6/LICENSE',timeout=60) as response:
                    (ROOT/'third-party-licenses/proxy_tools-Werkzeug-BSD.txt').write_bytes(response.read())
                found=True
            if not found:raise ValueError('Missing license text: '+name)
    with urllib.request.urlopen('https://raw.githubusercontent.com/shadcn-ui/ui/main/LICENSE.md',timeout=60) as response:
        (licenses/'shadcn-ui-LICENSE.txt').write_bytes(response.read())
    # WebView2 SDK assemblies shipped by pywebview (system Edge runtime is separate).
    url='https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/1.0.3856.49/microsoft.web.webview2.1.0.3856.49.nupkg'
    target=cache/'webview2-sdk.nupkg'
    if not target.exists():
        with urllib.request.urlopen(url,timeout=60) as response:target.write_bytes(response.read())
    with zipfile.ZipFile(target) as archive:
        names=[n for n in archive.namelist() if re.search(r'(license|thirdpartynotice)',n,re.I)]
        if not names:raise ValueError('WebView2 SDK license not found')
        for name in names:(ROOT/'third-party-licenses'/('WebView2-'+Path(name).name)).write_bytes(archive.read(name))
    print(f'Verified {len(entries)} npm runtime source archives; licenses collected; {output.name}')


if __name__=='__main__':main()
