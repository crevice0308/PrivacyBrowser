#!/usr/bin/env python3
"""Real Gecko smoke test over Marionette. Does not weaken Gecko sandboxing.

The test opens only privileged local UI unless --external-search is explicitly
requested. Native stderr is kept in a temporary file, not diagnostic packages.
All reported statuses distinguish failures, blocked tests and untested items.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time
import zipfile

class Marionette:
    def __init__(self, port, timeout=25):
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.sock = socket.create_connection(('127.0.0.1', port), timeout=2)
                self.sock.settimeout(25)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError('Marionette connection did not start')
                time.sleep(.15)
        self.seq = 0
        self.greeting = self.read()
    def read(self):
        length = b''
        while True:
            b = self.sock.recv(1)
            if not b:
                raise ConnectionError('Gecko connection closed')
            if b == b':':
                break
            length += b
        size = int(length)
        payload = bytearray()
        while len(payload) < size:
            b = self.sock.recv(size-len(payload))
            if not b:
                raise ConnectionError('Gecko connection closed')
            payload.extend(b)
        return json.loads(payload)
    def command(self, method, parameters=None):
        self.seq += 1
        body = json.dumps([0, self.seq, method, parameters or {}]).encode()
        self.sock.sendall(str(len(body)).encode()+b':'+body)
        response = self.read()
        if response[2]:
            raise RuntimeError(response[2].get('error', 'Marionette command failed') + ': ' + response[2].get('message','')[:1000])
        return response[3]
    def script(self, text, asynchronous=False):
        result = self.command('WebDriver:ExecuteAsyncScript' if asynchronous else 'WebDriver:ExecuteScript', {'script':text,'args':[],'newSandbox':True,'sandbox':'privacybrowser-test'})
        return result.get('value') if isinstance(result,dict) and 'value' in result else result

def run(binary, output, external_search=False):
    report = {'schema':1,'platform':os.uname().sysname if hasattr(os,'uname') else 'Windows', 'binary':Path(binary).name, 'sandbox_disabled':False, 'tests':[], 'official_release_qualified':False}
    proc = None
    client = None
    with tempfile.TemporaryDirectory(prefix='privacybrowser-smoke-') as temp:
        profile = Path(temp)/'profile';profile.mkdir()
        with socket.socket() as s:
            s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        (profile/'user.js').write_text(f'user_pref("marionette.port", {port});\nuser_pref("browser.startup.homepage", "chrome://privacybrowser/content/home.html");\n')
        stderr = Path(temp)/'native-stderr.txt'
        env = dict(os.environ)
        env['MOZ_CRASHREPORTER_DISABLE']='1'
        env['MOZ_MARIONETTE']='1'
        start = time.monotonic()
        try:
            with stderr.open('wb') as stream:
                proc = subprocess.Popen([str(Path(binary).resolve()),'--headless','--no-remote','--marionette','--remote-allow-system-access','--profile',str(profile),'about:license'],stdout=stream,stderr=stream,env=env)
                client = Marionette(port)
                session = client.command('WebDriver:NewSession',{'capabilities':{'alwaysMatch':{'acceptInsecureCerts':False,'pageLoadStrategy':'none'}}})
                report['session_created']=True
                report['version']=session.get('capabilities',{}).get('browserVersion')
                client.command('Marionette:SetContext',{'value':'chrome'})
                status = client.script('return {title:document.title,location:document.documentURI,tabCount:gBrowser.tabs.length,hasControls:!!document.getElementById("privacybrowser-search-button"),keyword:Services.prefs.getBoolPref("keyword.enabled"),sandboxLevel:Services.prefs.getIntPref("security.sandbox.content.level")};')
                report['chrome_status']=status
                report['tests'].append({'name':'window-controls','status':'pass' if status.get('hasControls') else 'fail'})
                report['tests'].append({'name':'URL-only-preference','status':'pass' if status.get('keyword') is False else 'fail'})
                inspection = client.script('''const done=arguments[arguments.length-1];
                  (async()=>{try{const m=ChromeUtils.importESModule("resource://privacybrowser/BrowserGlue.sys.mjs");
                  const p=ChromeUtils.importESModule("resource://privacybrowser/GeckoPrivacy.sys.mjs");
                  await p.PrivacyBrowserPrivacy.startup();
                  done({state:p.PrivacyBrowserPrivacy.controller.state, flags:p.PrivacyBrowserPrivacy.flags,glue:m.getStatus()});
                  }catch(e){done({error:String(e)});}})();''',True)
                report['privacy_runtime']=inspection
                report['tests'].append({'name':'privacy-startup','status':'pass' if inspection.get('state')=='ready' else 'fail'})
                ui = client.script('''const done=arguments[arguments.length-1];
                  const tab=gBrowser.addTrustedTab("chrome://privacybrowser/content/home.html",{inBackground:false});
                  gBrowser.selectedTab=tab;
                  let tries=0;
                  const timer=setInterval(()=>{const d=tab.linkedBrowser.contentDocument;
                    const input=d?.getElementById("search-input");
                    if(d?.documentElement?.dataset.privacyBrowserReady === "true" || ++tries>40){clearInterval(timer);
                      done({loaded:!!input,moduleExecuted:d?.documentElement?.dataset.privacyBrowserReady === "true",
                        principal:d?.nodePrincipal?.isSystemPrincipal??false,title:d?.title});}
                  },100);''',True)
                report['home_runtime']=ui
                report['tests'].append({'name':'privileged-home-search-UI','status':'pass' if ui.get('moduleExecuted') and ui.get('principal') else 'fail'})
                try:
                    shot=client.command('WebDriver:TakeScreenshot',{'id':None,'highlights':[],'full':False,'scroll':False})
                    payload=shot.get('value',shot) if isinstance(shot,dict) else shot
                    Path('/tmp/privacybrowser-qa-home.png').write_bytes(base64.b64decode(payload))
                    report['internal_screenshot_captured']=True
                except Exception:
                    report['internal_screenshot_captured']=False
                settings = client.script('''const done=arguments[arguments.length-1];
                  const tab=ChromeUtils.importESModule("resource://privacybrowser/WindowUI.sys.mjs").openSettings(window);
                  let tries=0;const timer=setInterval(()=>{const d=tab.linkedBrowser.contentDocument;
                    const engine=d?.getElementById("search-engine");
                    if(d?.documentElement?.dataset.privacyBrowserReady === "true" || ++tries>40){clearInterval(timer);
                      done({loaded:!!engine,moduleExecuted:d?.documentElement?.dataset.privacyBrowserReady === "true",privacyCheckboxes:d?.querySelectorAll('[data-pref^="history."]').length});}
                  },100);''',True)
                report['settings_runtime']=settings
                report['tests'].append({'name':'privileged-settings-UI','status':'pass' if settings.get('moduleExecuted') and settings.get('privacyCheckboxes')==8 else 'fail'})
                task = client.script('''const done=arguments[arguments.length-1];
                  const dialog=ChromeUtils.importESModule("resource://privacybrowser/TaskManager.sys.mjs").openTaskManager(window,gBrowser.selectedTab);
                  let tries=0;const timer=setInterval(()=>{const d=dialog.document;const rows=d?.querySelectorAll("#task-rows tr");
                    if(rows?.length || ++tries>50){clearInterval(timer);const result={rows:rows?.length??0,selected:!!d?.querySelector('[aria-selected="true"]'),
                      protectedClose:d?.getElementById("task-close")?.disabled??false};dialog.close();done(result);}
                  },100);''',True)
                report['tasks_runtime']=task
                report['tests'].append({'name':'native-task-manager','status':'pass' if task.get('rows',0)>0 and task.get('selected') and task.get('protectedClose') else 'fail'})
                privacy = client.script('''const done=arguments[arguments.length-1];
                  (async()=>{try{
                    const {PrivacyBrowserPrivacy:p}=ChromeUtils.importESModule("resource://privacybrowser/GeckoPrivacy.sys.mjs");
                    const {PlacesUtils}=ChromeUtils.importESModule("resource://gre/modules/PlacesUtils.sys.mjs");
                    const b=await PlacesUtils.bookmarks.insert({parentGuid:PlacesUtils.bookmarks.unfiledGuid,url:"https://privacybrowser.invalid/bookmark",title:"PB smoke bookmark"});
                    Services.cookies.add("privacybrowser.invalid","/","pb_smoke","marker",true,false,false,Math.floor(Date.now()/1000)+3600,{},0,2);
                    await p.configure({search:true});
                    await p.recordSearch("日本語テスト");
                    const unicode=(await p.readSearchHistory())[0]==="日本語テスト";
                    await p.configure({search:false});
                    await p.clearAll();
                    done({unicodeSearchHistory:unicode,searchCleared:(await p.readSearchHistory()).length===0,
                      cookieCleared:!Services.cookies.cookieExists("privacybrowser.invalid","/","pb_smoke",{}),
                      bookmarkRetained:!!await PlacesUtils.bookmarks.fetch(b.guid),bookmarkGuid:b.guid,
                      diagnosticsDefaultOff:!Services.prefs.getBoolPref("privacybrowser.diagnostics.enabled")});
                  }catch(e){done({error:String(e),failureDetails:e.failureDetails});}})();''',True)
                report['privacy_data_runtime']=privacy
                for key in ['unicodeSearchHistory','searchCleared','cookieCleared','bookmarkRetained','diagnosticsDefaultOff']:
                    report['tests'].append({'name':key,'status':'pass' if privacy.get(key) is True else 'fail'})
                diag = client.script('''const done=arguments[arguments.length-1];
                  (async()=>{try{const {GeckoDiagnosticsSink}=ChromeUtils.importESModule("resource://privacybrowser/GeckoPrivacy.sys.mjs");
                    const {DiagnosticsLogger}=ChromeUtils.importESModule("resource://privacybrowser/Diagnostics.sys.mjs");
                    const sink=new GeckoDiagnosticsSink(PathUtils.join(PathUtils.profileDir,"qa-diagnostics"));
                    const logger=new DiagnosticsLogger({sink,enabled:true,level:"debug",maxBytes:1024,maxFiles:2});
                    for(let i=0;i<20;i++) await logger.log("resource-sample",{pid:Services.appinfo.processID,residentBytes:1024+i,url:"https://diagnostic-secret.invalid",password:"diagnostic-secret",query:"diagnostic-secret"},"debug");
                    const destination=PathUtils.join(PathUtils.profileDir,"qa-diagnostics.zip");
                    await logger.exportPackage(destination,{userInitiated:true});
                    const files=await IOUtils.getChildren(sink.directory);
                    const bounded=files.length<=2 && (await Promise.all(files.map(x=>IOUtils.stat(x)))).every(x=>x.size<=1024);
                    await logger.deleteLogs();
                    done({exported:await IOUtils.exists(destination),bounded,deleted:(await IOUtils.getChildren(sink.directory)).length===0});
                  }catch(e){done({error:String(e)});}})();''',True)
                report['diagnostics_runtime']=diag
                for key in ['exported','bounded','deleted']:
                    report['tests'].append({'name':'native-diagnostics-'+key,'status':'pass' if diag.get(key) is True else 'fail'})
                if diag.get('exported'):
                    with zipfile.ZipFile(profile/'qa-diagnostics.zip') as z:
                        package_safe=set(z.namelist())=={'manifest.json','diagnostics.jsonl'} and all(b'diagnostic-secret' not in z.read(n) for n in z.namelist())
                    report['tests'].append({'name':'native-diagnostics-ZIP-no-secret','status':'pass' if package_safe else 'fail'})
                resources=client.script('''const done=arguments[arguments.length-1];
                  ChromeUtils.requestProcInfo().then(p=>done({parentResidentBytes:p.memory,parentPid:p.pid,
                    childCount:p.children.length,representativeOfNormalBrowsing:false})).catch(()=>done({unavailable:true}));''',True)
                if 'parentResidentBytes' in resources:
                    resources['nativeReportedMemoryBytes']=resources.pop('parentResidentBytes')
                try:
                    os_status=Path(f'/proc/{proc.pid}/status').read_text()
                    resources['osParentRssBytes']=int(next(x for x in os_status.splitlines() if x.startswith('VmRSS:')).split()[1])*1024
                except (OSError,StopIteration,ValueError):
                    pass
                resources['validForMemoryTarget']=False
                resources['reason']='Parent/chrome smoke with sandbox-denied content crashes; raw values do not qualify normal-browser memory or CPU targets.'
                report['smoke_resources']=resources
                if external_search:
                    external=client.script('''const done=arguments[arguments.length-1];
                      const tab=ChromeUtils.importESModule("resource://privacybrowser/WindowUI.sys.mjs").openSearch(window);
                      const d=tab.linkedBrowser.contentDocument;const input=d.getElementById("search-input");
                      input.value="Privacy Browser";d.getElementById("search-form").requestSubmit();
                      setTimeout(()=>done({finalURI:tab.linkedBrowser.currentURI.spec,crashed:tab.hasAttribute("crashed"),status:d.getElementById("status")?.textContent,dispatched:d.documentElement.dataset.searchDispatched==="true"}),2000);''',True)
                    report['external_search_attempt']=external
                    report['tests'].append({'name':'Brave-search-dispatch','status':'pass' if external.get('dispatched') else 'fail'})
                    report['external_search_matrix']=[{'term':term,'status':'blocked','attempted':index==0,'resultsRendered':False} for index,term in enumerate(['Privacy Browser','Ubuntu Linux','japan no1','あいうえお','日本語テスト','Firefox ESR'])]
                    report['tests'].append({'name':'Brave-search-results','status':'blocked','reason':'Content rendering in this execution sandbox is not qualified; search results not validated.'})
                errors = client.script('''return Services.console.getMessageArray().filter(x=>/privacybrowser|Privacy Browser/.test(x.message||x.errorMessage||"")).map(x=>({message:(x.message||x.errorMessage||"").slice(0,600)})).slice(-20);''')
                report['integration_console_messages']=errors
                try:
                    client.script('setTimeout(()=>window.close(),250); return true;')
                    # End Marionette while certificate services are still live;
                    # the queued window close then exercises the real UI path.
                    client.command('WebDriver:DeleteSession')
                except (ConnectionError,OSError):
                    pass
                try:
                    proc.wait(timeout=15)
                    report['tests'].append({'name':'last-window-close-exit','status':'pass' if proc.returncode==0 else 'fail'})
                    report['tests'].append({'name':'native-process-exit','status':'pass','exit_code':proc.returncode})
                    sessions = (profile/'sessionstore.jsonlz4').exists() or any((profile/'sessionstore-backups').glob('*'))
                    report['tests'].append({'name':'no-session-files-after-exit','status':'fail' if sessions else 'pass'})
                    report['profile_after_exit']={'sessionFilesPresent':sessions,'diagnosticDirectoryPresent':(profile/'privacybrowser-diagnostics').exists(),'searchHistoryPresent':(profile/'privacybrowser-search-history.json').exists()}
                    db=profile/'places.sqlite'
                    if db.exists():
                        with sqlite3.connect(db) as con:
                            bookmark_count=con.execute('SELECT count(*) FROM moz_bookmarks WHERE guid=?',(privacy.get('bookmarkGuid',''),)).fetchone()[0]
                            report['tests'].append({'name':'bookmark-persisted-after-exit','status':'pass' if bookmark_count==1 else 'fail'})
                except subprocess.TimeoutExpired:
                    report['tests'].append({'name':'native-process-exit','status':'fail'})
        except Exception as e:
            report['error_type']=type(e).__name__
            report['error']=str(e)[:1500]
            report['tests'].append({'name':'native-integration','status':'blocked'})
        finally:
            if client:
                client.sock.close()
            if proc and proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
            text=stderr.read_text(errors='replace') if stderr.exists() else ''
            report['environment_sandbox_denied']=('uid_map: EPERM' in text or 'CanCreateUserNamespace() clone() failure' in text)
            report['native_content_crash_detected']=('exited on signal' in text)
            report['native_error_summary']=[line[:500] for line in text.splitlines() if any(x in line for x in ['Privacy Browser','autoconfig','JavaScript error','SyntaxError','TypeError','ReferenceError'])][-30:]
            report['unresolved_native_errors']=bool(report['native_error_summary'])
            report['elapsed_seconds']=round(time.monotonic()-start,3)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if not any(t['status'] in ['fail','blocked'] for t in report['tests']) else 1

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',required=True);p.add_argument('--output',type=Path,default=Path('tests/results/linux-integration.json'));p.add_argument('--external-search',action='store_true');a=p.parse_args()
    raise SystemExit(run(a.binary,a.output,a.external_search))
