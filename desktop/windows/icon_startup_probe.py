"""Observe capsule images during isolated, installed Windows App cold starts."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

sys.path.insert(0, str(Path.cwd()))
from desktop.windows import verify_native as native
from desktop.windows import verify_portable as portable
from desktop.windows.webview_test_environment import webview_test_environment

NAMES = {"cap_coin.png", "cap_paw.png", "pk_words.png", "cap_compass.png",
         "cap_cookie.png", "cap_diamond.png"}

INIT = r"""(() => {
  if (window.__qpetIconProbe) return;
  const names = new Set(['cap_coin.png','cap_paw.png','pk_words.png',
    'cap_compass.png','cap_cookie.png','cap_diamond.png']);
  const trace = window.__qpetIconProbe = {
    installedUnixMs: Date.now(), installedAt: performance.now(),
    installedReadyState: document.readyState, existingIconCount: 0,
    timeOrigin: performance.timeOrigin, events: []
  };
  const seen = new WeakSet();
  const relevant = (img) => img instanceof HTMLImageElement && img.matches('.caps img.cico') &&
    names.has((img.currentSrc || img.src || '').split('?')[0].split('/').pop());
  const state = (img) => ({
    src: img.getAttribute('src'), currentSrc: img.currentSrc,
    complete: img.complete, naturalWidth: img.naturalWidth,
    naturalHeight: img.naturalHeight
  });
  const push = (kind, img, extra = {}) => {
    if (!relevant(img) || trace.events.length >= 120) return;
    trace.events.push({kind, at: performance.now(), unixMs: Date.now(), ...state(img), ...extra});
  };
  const watch = () => document.querySelectorAll('.caps img.cico').forEach(img => {
    if (!seen.has(img)) { seen.add(img); push('insert-observed', img); }
  });
  trace.existingIconCount = document.querySelectorAll('.caps img.cico').length;
  document.addEventListener('load', e => push('load', e.target), true);
  document.addEventListener('error', e => push('error', e.target), true);
  new MutationObserver(watch).observe(document, {subtree:true, childList:true, attributes:true,
    attributeFilter:['src']});
  watch();
  window.__qpetDecodeIcons = async () => {
    const icons = [...document.querySelectorAll('.caps img.cico')];
    return Promise.all(icons.map(async img => {
      push('decode-start', img);
      try { await img.decode(); push('decode-ok', img); return {ok:true, ...state(img)}; }
      catch (e) { push('decode-error', img, {errorName:e?.name || 'Error'});
        return {ok:false, ...state(img)}; }
    }));
  };
})()"""

SNAPSHOT = r"""() => ({
  unixMs: Date.now(), at: performance.now(), readyState: document.readyState,
  dpr: devicePixelRatio, viewport: [innerWidth, innerHeight],
  trace: window.__qpetIconProbe || null,
  icons: [...document.querySelectorAll('.caps img.cico')].map(img => {
    const css = getComputedStyle(img), rect = img.getBoundingClientRect();
    const ring = img.parentElement?.querySelector('.cring');
    return {src:img.getAttribute('src'), currentSrc:img.currentSrc, complete:img.complete,
      naturalWidth:img.naturalWidth, naturalHeight:img.naturalHeight,
      rect:[rect.x,rect.y,rect.width,rect.height], opacity:css.opacity,
      visibility:css.visibility, display:css.display, transform:css.transform,
      ringMask:ring ? getComputedStyle(ring).maskImage : null};
  }),
  resources: performance.getEntriesByType('resource').filter(x =>
    /\/qp-icons\/official\/(cap_coin|cap_paw|pk_words|cap_compass|cap_cookie|cap_diamond)\.png/.test(x.name))
    .map(x => ({name:x.name,startTime:x.startTime,responseEnd:x.responseEnd,
      duration:x.duration,transferSize:x.transferSize,encodedBodySize:x.encodedBodySize,
      decodedBodySize:x.decodedBodySize,initiatorType:x.initiatorType}))
})"""


def icon_url(value: str) -> bool:
    parts = urlsplit(value)
    return parts.path.startswith('/qp-icons/official/') and parts.path.rsplit('/', 1)[-1] in NAMES


def wait(callback, timeout, message):
    return native._wait(callback, timeout, message)


def capture(desktop, hwnd: int, destination: Path) -> dict:
    """Capture promptly; NativeDesktop.capture_window intentionally waits 250ms."""
    from PIL import ImageGrab
    started = time.time() * 1000
    dpi = getattr(desktop.user, 'SetThreadDpiAwarenessContext', None)
    previous = None
    if dpi:
        native._api(desktop.user, 'SetThreadDpiAwarenessContext', wintypes.HANDLE, wintypes.HANDLE)
        previous = dpi(ctypes.c_void_p(-4))
    try:
        bounds = wintypes.RECT()
        if not desktop.user.GetWindowRect(hwnd, ctypes.byref(bounds)):
            raise portable.VerificationError('cannot capture the owned app window')
        rectangle = (bounds.left, bounds.top, bounds.right, bounds.bottom)
        image = ImageGrab.grab(bbox=rectangle, all_screens=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, format='PNG')
        return {'file':destination.name, 'startedUnixMs':started, 'finishedUnixMs':time.time()*1000,
                'width':image.width, 'height':image.height,
                'foreground':desktop.user.GetForegroundWindow() == hwnd}
    finally:
        if dpi and previous:
            dpi(previous)


def trial(executable: Path, directory: Path, number: int, desktop, playwright_factory) -> dict:
    result = {'trial':number, 'ok':False, 'responses':[], 'requestFailures':[], 'shots':[],
              'samples':[], 'pageInstrumentation':[], 'errors':[]}
    temporary = tempfile.TemporaryDirectory(prefix=f'qqpet-icon-probe-{number}-')
    sandbox = Path(temporary.name)
    data = sandbox/'data'; runs = data/'runs'; runs.mkdir(parents=True)
    (runs/'onboarding.json').write_text('{"version":1,"completed":true}\n', encoding='utf-8')
    port, debug = native._port(), native._port()
    while debug == port: debug = native._port()
    server = f'http://127.0.0.1:{port}/'
    fingerprint = native._fingerprint(executable.parent/'backend')
    store = sandbox/'runtime-cache'
    expected_backend = store/fingerprint/'QQPetCopilot.exe'
    env = portable.child_environment(data)
    env.update({'QQPET_SERVER':server, 'QQPET_TRANSIENT':'0', 'QQPET_RUNTIME_STORE':str(store),
        'QQPET_SOURCE_ROOT':str(sandbox/'no-source'),
        'WEBVIEW2_USER_DATA_FOLDER':str(sandbox/'webview-data'),
        'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS':
            f'--remote-debugging-port={debug} --remote-debugging-address=127.0.0.1'})
    opener = build_opener(ProxyHandler({}))
    debug_url = f'http://127.0.0.1:{debug}'
    gui, backend, browser = None, None, None
    started = int((time.time()+native.FILETIME_EPOCH-2)*10_000_000)
    policy = ExitStack()
    stage = 'launch'
    log = (sandbox/'gui.log').open('wb')
    secrets = []

    def status(base, path):
        try: return portable._request(opener, base.rstrip('/'), path)[0]
        except (OSError, URLError, TimeoutError): return None

    def main_window():
        candidates = [w for w in desktop.windows(gui.pid)
                      if w['visible'] and w['title'] == native.TITLE]
        return candidates[0] if len(candidates) == 1 else None

    def setup_context(context):
        if getattr(context, '_qpet_icon_probe_setup', False): return
        context._qpet_icon_probe_setup = True
        context.add_init_script(INIT)

        context.route('**/api/screenshot*', lambda route: route.fulfill(
            status=503, content_type='application/json',
            body='{"ok":false,"error":"isolated icon probe"}'))
        context.route('**/api/runner/**', lambda route: route.abort())
        context.on('page', setup_page)
        for existing in context.pages: setup_page(existing)

    def setup_page(page):
        if getattr(page, '_qpet_icon_probe_setup', False): return
        page._qpet_icon_probe_setup = True
        result['pageInstrumentation'].append({'atUnixMs':time.time()*1000,
                                             'url':native._normalized_url(page.url)})
        def response(response):
            if icon_url(response.url):
                headers = response.headers
                result['responses'].append({'atUnixMs':time.time()*1000,
                    'url':native._normalized_url(response.url), 'status':response.status,
                    'contentType':headers.get('content-type'), 'contentLength':headers.get('content-length'),
                    'fromServiceWorker':response.from_service_worker})
        def failed(request):
            if icon_url(request.url):
                result['requestFailures'].append({'atUnixMs':time.time()*1000,
                    'url':native._normalized_url(request.url), 'failure':request.failure})
        page.on('response', response)
        page.on('requestfailed', failed)
        try: page.evaluate(INIT)
        except Exception: pass  # init script still covers this page's next document

    try:
        result['policy'] = policy.enter_context(webview_test_environment(
            executable, sandbox/'webview-data', env['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'], environ=env))
        gui = subprocess.Popen([str(executable)], cwd=sandbox, env=env,
                               stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        result['launchedUnixMs'] = time.time()*1000
        stage = 'early-cdp'
        wait(lambda:status(debug_url,'/json/version') == 200, 45, 'CDP unavailable')
        result['cdpReadyUnixMs'] = time.time()*1000
        with playwright_factory() as playwright:
            browser = playwright.chromium.connect_over_cdp(debug_url, timeout=20000)
            for context in browser.contexts: setup_context(context)
            browser.on('context', setup_context)
            session = browser.new_browser_cdp_session()
            stage = 'backend-ownership'
            pid = wait(lambda:native._pid_file(runs/'dashboard.pid'), 75, 'backend PID missing')
            backend = desktop.claim_backend(pid, gui.pid, expected_backend, started)
            result['backendPID'] = pid
            for p in runs.glob('*.token'):
                value = p.read_text(encoding='utf-8').strip()
                if value: secrets.append(value)
            stage = 'capsule-first-frame'
            def ready():
                session.send('Target.getTargets')
                for context in browser.contexts: setup_context(context)
                page = native._dashboard_page(browser, server)
                window = main_window()
                if page and window:
                    state = page.evaluate('() => document.querySelectorAll(".caps img.cico").length')
                    if state == 6: return page, window
                return None
            page, window = wait(ready, 45, 'capsules never mounted in a visible owned window')
            first = time.monotonic()
            result['firstCapsVisibleUnixMs'] = time.time()*1000
            result['window'] = window
            result['shots'].append(capture(desktop,window['hwnd'],directory/f'trial-{number}-first.png'))
            state = page.evaluate(SNAPSHOT); state['label'] = 'first-visible'; result['samples'].append(state)
            for seconds in (0.5,1.0,2.0):
                while time.monotonic()-first < seconds:
                    page.wait_for_timeout(min(40,max(1,int((seconds-(time.monotonic()-first))*1000))))
                result['shots'].append(capture(desktop,window['hwnd'],directory/f'trial-{number}-{seconds:g}s.png'))
                state=page.evaluate(SNAPSHOT); state['label']=f'{seconds:g}s';result['samples'].append(state)
            stage='decode-observation'
            result['decodes'] = page.evaluate('''async () => {
                const decode = window.__qpetDecodeIcons?.();
                if (!decode) return {probeMissing:true};
                return await Promise.race([decode, new Promise(r=>setTimeout(()=>r({timedOut:true}),15000))]);
            }''')
            page.evaluate('() => new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
            result['shots'].append(capture(desktop,window['hwnd'],directory/f'trial-{number}-decoded.png'))
            final=page.evaluate(SNAPSHOT);final['label']='after-decode';result['samples'].append(final)
            result['instrumentedBeforeIcons'] = bool(final['trace'] and final['trace']['existingIconCount']==0)
            result['initialDocumentMissed'] = not result['instrumentedBeforeIcons']
            result['cache'] = native._check_cached_backend(executable.parent/'backend', expected_backend.parent)
            state = page.evaluate('''async () => {
              const r=await fetch('/api/data'); const d=await r.json();
              return {status:r.status, schedulerAlive:d.scheduler?.alive, schedulerPid:d.scheduler?.pid};
            }''')
            if state != {'status':200,'schedulerAlive':False,'schedulerPid':None} or (runs/'runner.pid').exists():
                raise portable.VerificationError('probe unexpectedly found a running scheduler')
            result['scheduler'] = state
            result['ok'] = len(final['icons'])==6
            result['allImagesDecoded'] = all(i['complete'] and i['naturalWidth']==96 and i['naturalHeight']==96
                                             for i in final['icons'])
    except Exception as exc:
        result['errors'].append({'stage':stage,'type':type(exc).__name__,
            'message':native._safe_text(str(exc),secrets,700)})
    finally:
        try:
            if backend is None and gui is not None:
                pid=native._pid_file(runs/'dashboard.pid')
                if pid:
                    try:backend=desktop.claim_backend(pid,gui.pid,expected_backend,started)
                    except (OSError,RuntimeError):pass
            try:
                if gui is not None:desktop.cleanup_gui(gui)
            finally:
                if backend is not None:desktop.cleanup_backend(backend)
        except Exception as exc:
            result['ok']=False;result['errors'].append({'stage':'process-cleanup','type':type(exc).__name__})
        finally:
            try:policy.close()
            except Exception as exc:
                result['ok']=False;result['errors'].append({'stage':'policy-cleanup','type':type(exc).__name__})
            log.close()
            try:result['cleanupAttempts']=native._cleanup_temporary(temporary)
            except Exception as exc:
                result['ok']=False;result['errors'].append({'stage':'temp-cleanup','type':type(exc).__name__})
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--output-directory',type=Path,required=True)
    parser.add_argument('--trials',type=int,default=3)
    args=parser.parse_args()
    executable=args.executable.resolve(); report_path=args.report.resolve();output=args.output_directory.resolve()
    if report_path.is_relative_to(executable.parent) or output.is_relative_to(executable.parent):
        parser.error('diagnostic output must be outside the installed App directory')
    if not 1<=args.trials<=5:parser.error('--trials must be between 1 and 5')
    result={'ok':False,'platform':sys.platform,'trials':[]}
    try:
        if sys.platform!='win32':raise portable.VerificationError('actual Windows is required')
        result['executable']=portable.check_pe(executable)
        desktop=native.NativeDesktop();result['desktop']=desktop.desktop_evidence()
        from playwright.sync_api import sync_playwright
        output.mkdir(parents=True,exist_ok=True)
        for number in range(1,args.trials+1):
            result['trials'].append(trial(executable,output,number,desktop,sync_playwright))
            report_path.parent.mkdir(parents=True,exist_ok=True)
            report_path.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        result['ok']=all(t['ok'] for t in result['trials'])
    except Exception as exc:
        result['error']={'type':type(exc).__name__,'message':str(exc) if isinstance(exc,portable.VerificationError) else ''}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'ok':result['ok'],'trials':[{'trial':t['trial'],'ok':t['ok'],
        'initialDocumentMissed':t.get('initialDocumentMissed'),
        'allImagesDecoded':t.get('allImagesDecoded'),'errors':t['errors']} for t in result['trials']]}))
    return 0 if result['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
