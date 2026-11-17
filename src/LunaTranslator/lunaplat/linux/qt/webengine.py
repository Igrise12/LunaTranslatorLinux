"""QtWebEngine stand-in for the Windows web views (WebView2 / EdgeHTML).

gui.usefulwidget's web view widgets talk to a small "native web view" object
(`self.webview`): setHtml, navigate, eval (synchronous when a callback is
given, the callback receiving the JSON text of the result), bind (JS ->
Python via window.LUNAJSObject.<name>(...)), zoom, a context-menu handler
(on_menu), resize and a set of event callbacks. WebEngineView implements that
object on top of a QWebEngineView child of the widget.

* JS -> Python: a QWebChannel object plus a document-creation script that
  defines window.LUNAJSObject.<name> for every bound name, exactly like the
  WebView2 build. Calls made before the channel is connected are queued.
* Transparency: the page background is set to transparent (the main window
  renderer relies on a see-through overlay).
* prefers-color-scheme: Chromium only takes it at start-up, so the app theme
  (ui_settings.darklight2) is turned into --blink-settings when the web
  engine first starts. Changing the theme later needs a restart for web
  content.

Qt requires Qt.AA_ShareOpenGLContexts before the QApplication exists when
QtWebEngineWidgets is imported later; lunaplat.linux.install_linux() sets it.
"""

import hashlib
import json
import os
import sys
from traceback import print_exc

from PyQt6.QtCore import (
    QEventLoop,
    QObject,
    Qt,
    QTimer,
    QUrl,
    pyqtSlot,
)

DEBUG = os.environ.get("LUNA_WEBVIEW_DEBUG") == "1"

# Context-menu entries of WebView2 browser extensions (not available here).
HIDDEN_MENU_TEXTS = {"附加浏览器插件", "浏览器插件"}

# QWebEngineView.setHtml goes through a percent-encoded data: URL limited to
# 2 MB; larger documents are written to a temporary file instead.
_DATA_URL_LIMIT = 2 * 1024 * 1024 - 4096

_import_error = None
_profile = None
_channel_js = None


def _modules():
    global _import_error
    try:
        from PyQt6 import QtWebChannel, QtWebEngineCore, QtWebEngineWidgets

        return QtWebEngineCore, QtWebEngineWidgets, QtWebChannel
    except Exception as e:  # missing PyQt6-WebEngine, or imported too late
        _import_error = e
        return None


def available():
    return _modules() is not None


def unavailable_reason():
    _modules()
    return str(_import_error) if _import_error else ""


def _chromium_flags(darklight):
    """darklight: 0 follow system, 1 light, 2 dark (ui_settings.darklight2).
    Blink's preferredColorScheme: 0 = dark, 1 = light."""
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if "preferredColorScheme" in flags or darklight not in (1, 2):
        return
    scheme = 1 if darklight == 1 else 0
    flags = (flags + " --blink-settings=preferredColorScheme={}".format(scheme)).strip()
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags


def _xdg(kind, fallback):
    base = os.environ.get(kind) or os.path.expanduser(fallback)
    return os.path.join(base, "LunaTranslator", "webengine")


def profile(darklight=0):
    """Persistent profile shared by every view (cookies, local storage),
    like WebView2's user data folder in %APPDATA%\\LunaTranslator."""
    global _profile
    if _profile is not None:
        return _profile
    core = _modules()[0]
    _chromium_flags(darklight)
    p = core.QWebEngineProfile("LunaTranslator")
    try:
        p.setPersistentStoragePath(_xdg("XDG_DATA_HOME", "~/.local/share"))
        p.setCachePath(_xdg("XDG_CACHE_HOME", "~/.cache"))
    except Exception:
        print_exc()
    s = p.settings()
    A = core.QWebEngineSettings.WebAttribute
    # Same rules as the WebView2 build (Chromium defaults): local pages may
    # load their own subresources (<script src>, <img>), but scripts cannot
    # read other files or remote URLs through XHR/fetch. Dictionary pages
    # show third-party HTML as local files, so file access + remote access
    # would let such content read and send out local files.
    s.setAttribute(A.LocalContentCanAccessFileUrls, False)
    s.setAttribute(A.LocalContentCanAccessRemoteUrls, False)
    s.setAttribute(A.AllowRunningInsecureContent, False)
    s.setAttribute(A.JavascriptCanPaste, False)
    # copy buttons in pages still work (clipboard write)
    s.setAttribute(A.JavascriptCanAccessClipboard, True)
    s.setAttribute(A.FocusOnNavigationEnabled, False)
    s.setAttribute(A.NavigateOnDropEnabled, False)
    _profile = p
    return p


def _qwebchannel_js():
    global _channel_js
    if _channel_js is None:
        from PyQt6.QtCore import QFile, QIODevice

        f = QFile(":/qtwebchannel/qwebchannel.js")
        if f.open(QIODevice.OpenModeFlag.ReadOnly):
            _channel_js = bytes(f.readAll()).decode("utf-8")
            f.close()
        else:
            _channel_js = ""
    return _channel_js


_BOOTSTRAP = """
(function () {
  if (!window.__lunaBridge) {
    var queue = [];
    var target = null;
    var send = function (name, args) {
      var payload = JSON.stringify(args);
      if (target) target.call(name, payload);
      else queue.push([name, payload]);
    };
    window.__lunaBridge = { send: send };
    try {
      %(channeljs)s
      new QWebChannel(qt.webChannelTransport, function (channel) {
        target = channel.objects.luna;
        for (var i = 0; i < queue.length; i++) target.call(queue[i][0], queue[i][1]);
        queue = [];
      });
    } catch (e) { console.error('luna bridge: ' + e); }
  }
  window.LUNAJSObject = window.LUNAJSObject || {};
  %(names)s.forEach(function (name) {
    window.LUNAJSObject[name] = function () {
      window.__lunaBridge.send(name, Array.prototype.slice.call(arguments));
    };
  });
  window.chrome = window.chrome || {};
  if (!window.chrome.webview) {
    window.chrome.webview = { postMessage: function () {},
                              postMessageWithAdditionalObjects: function () {} };
  }
})();
"""

# Evaluate a script like WebView2's ExecuteScript: the completion value of
# the script, serialised as JSON ("null" for undefined or errors).
_EVAL_WRAPPER = """
(function () {
  var r;
  try { r = (0, eval)(%s); } catch (e) { return "null"; }
  if (r === undefined) return "null";
  try { var s = JSON.stringify(r); return s === undefined ? "null" : s; }
  catch (e) { return "null"; }
})()
"""


def _in_gui_thread():
    from lunaplat.linux.qtthread import in_gui_thread

    return in_gui_thread()


def _to_qurl(url):
    url = str(url)
    if url.startswith(("file:", "http:", "https:", "about:", "data:", "qrc:")):
        return QUrl(url)
    if os.path.isabs(url) or os.path.exists(url):
        return QUrl.fromLocalFile(os.path.abspath(url))
    return QUrl.fromUserInput(url)


def _open_external(url):
    url = str(url)
    if not url or url.startswith(("about:", "data:", "javascript:")):
        return
    try:
        os.startfile(url)
    except Exception:
        print_exc()


class _Bridge(QObject):
    def __init__(self, owner):
        super().__init__()
        self._owner = owner

    @pyqtSlot(str, str)
    def call(self, name, payload):
        self._owner._dispatch(name, payload)


def _make_classes():
    core, widgets, channel = _modules()

    class PopupPage(core.QWebEnginePage):
        """target=_blank / window.open: open in the desktop browser, as the
        WebView2 build does (ShellExecute)."""

        def acceptNavigationRequest(self, url, navtype, ismainframe):
            if ismainframe:
                _open_external(url.toString())
                QTimer.singleShot(0, self.deleteLater)
                return False
            return True

    class Page(core.QWebEnginePage):
        def __init__(self, prof, view, owner):
            super().__init__(prof, view)
            self._owner = owner

        def acceptNavigationRequest(self, url, navtype, ismainframe):
            if ismainframe:
                self._owner._navigating(url)
            return True

        def createWindow(self, wtype):
            return PopupPage(self.profile(), self)

        def javaScriptConsoleMessage(self, level, message, line, source):
            if DEBUG:
                sys.stderr.write("[webview] {}:{} {}\n".format(source, line, message))

    class View(widgets.QWebEngineView):
        def __init__(self, parent, owner):
            super().__init__(parent)
            self._owner = owner
            self.setAcceptDrops(True)

        def contextMenuEvent(self, ev):
            self._owner._context_menu(ev.globalPos())

        def dragEnterEvent(self, ev):
            if ev.mimeData().hasUrls():
                ev.acceptProposedAction()
                return
            super().dragEnterEvent(ev)

        def dragMoveEvent(self, ev):
            if ev.mimeData().hasUrls():
                ev.acceptProposedAction()
                return
            super().dragMoveEvent(ev)

        def dropEvent(self, ev):
            files = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
            if files:
                ev.acceptProposedAction()
                for f in files:
                    self._owner._files_dropped(f)
                return
            super().dropEvent(ev)

    return Page, View, channel.QWebChannel


_classes = None


class WebEngineView(object):
    """The `webview` object of gui.usefulwidget's web view widgets."""

    def __init__(self, host, transp=False, darklight=0):
        global _classes
        if not available():
            raise RuntimeError("QtWebEngine is not available: " + unavailable_reason())
        if _classes is None:
            _classes = _make_classes()
        Page, View, QWebChannel = _classes
        self.html_limit = _DATA_URL_LIMIT
        self.host = host
        self.binds = {}
        self.on_menu = lambda selecttext: []
        self._dead = False
        self._zoom = 1.0
        self._script = None
        self.zoomchange_callback = None
        self.navigating_callback = None
        self.FilesDropped_callback = None
        self.titlechange_callback = None
        self.IconChanged_callback = None
        self.Crashed_Callback = None

        prof = profile(darklight)
        self.view = View(host, self)
        self.page = Page(prof, self.view, self)
        self.view.setPage(self.page)
        self._bridge = _Bridge(self)
        self._channel = QWebChannel(self.page)
        self._channel.registerObject("luna", self._bridge)
        self.page.setWebChannel(self._channel)
        if transp:
            self.page.setBackgroundColor(Qt.GlobalColor.transparent)
        self._install_script()
        self.view.titleChanged.connect(self._title_changed)
        self.view.iconChanged.connect(self._icon_changed)
        self.page.loadFinished.connect(self._load_finished)
        self.page.renderProcessTerminated.connect(self._render_terminated)
        try:
            self.page.zoomFactorChanged.connect(self._zoom_changed)
        except AttributeError:  # Qt < 6.8
            pass
        self.view.destroyed.connect(self._view_destroyed)
        self.view.setGeometry(host.rect())
        self.view.show()

    # ---- AbstractWebView API ---------------------------------------------

    def set_callbacks(
        self,
        zoomchange_callback=None,
        navigating_callback=None,
        FilesDropped_callback=None,
        titlechange_callback=None,
        IconChanged_callback=None,
        Crashed_Callback=None,
    ):
        """IconChanged_callback receives a QIcon, Crashed_Callback bytes."""
        self.zoomchange_callback = zoomchange_callback
        self.navigating_callback = navigating_callback
        self.FilesDropped_callback = FilesDropped_callback
        self.titlechange_callback = titlechange_callback
        self.IconChanged_callback = IconChanged_callback
        self.Crashed_Callback = Crashed_Callback

    def setfocus(self):
        if not self._dead:
            self.view.setFocus()

    def destroy(self):
        self._dead = True

    def resize(self, w=None, h=None):
        if self._dead:
            return
        self.view.setGeometry(self.host.rect())

    def put_PreferredColorScheme(self, darklight):
        # Chromium takes the colour scheme only at start-up (see profile()).
        pass

    def get_zoom(self):
        if self._dead:
            return self._zoom
        return self.view.zoomFactor()

    def set_zoom(self, zoom):
        try:
            zoom = float(zoom)
        except (TypeError, ValueError):
            return
        zoom = min(5.0, max(0.25, zoom))
        self._zoom = zoom
        if not self._dead:
            self.view.setZoomFactor(zoom)

    def navigate(self, url):
        if not self._gui(self.navigate, url):
            return
        self.page.load(_to_qurl(url))

    def setHtml(self, html):
        if not self._gui(self.setHtml, html):
            return
        # percent-encoding: up to 3 characters per UTF-8 byte
        if len(html) * 9 > self.html_limit and len(html.encode("utf8")) * 3 > self.html_limit:
            import gobject

            md5 = hashlib.md5(html.encode("utf8", errors="ignore")).hexdigest()
            path = gobject.gettempdir(md5 + ".html")
            with open(path, "w", encoding="utf8") as ff:
                ff.write(html)
            self.page.load(QUrl.fromLocalFile(os.path.abspath(path)))
            return
        self.page.setHtml(html, QUrl.fromLocalFile(os.path.abspath(".") + "/"))

    def bind(self, fname, func):
        self.binds[fname] = func
        if self._dead:
            return
        self._install_script()
        # also define it in the current document (WebView2 only does it for
        # documents created later, which is a subset of this)
        self.page.runJavaScript(self._script_source())

    def eval(self, js, callback=None):
        """Run js. With a callback, wait for the result (like WebView2) and
        pass its JSON text to callback."""
        if not _in_gui_thread():
            from lunaplat.linux.qtthread import post_to_gui, run_in_gui

            if callback is None:
                post_to_gui(lambda: self.eval(js))
            else:
                run_in_gui(lambda: self.eval(js, callback), timeout=10)
            return
        if self._dead:
            return
        if callback is None:
            self.page.runJavaScript(js)
            return
        box = []
        loop = QEventLoop()

        def done(result):
            box.append(result)
            if loop.isRunning():
                loop.quit()

        self.page.runJavaScript(_EVAL_WRAPPER % json.dumps(js), 0, done)
        if not box:
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            timer.start(5000)
            loop.exec()
            timer.stop()
        if not box:
            return
        result = box[0]
        if not isinstance(result, str):
            result = json.dumps(result)
        callback(result)

    # ---- helpers -----------------------------------------------------------

    def _gui(self, fn, *args):
        """True when the caller may proceed on this (GUI) thread; otherwise
        the call is re-posted to the GUI thread."""
        if self._dead:
            return False
        if _in_gui_thread():
            return True
        from lunaplat.linux.qtthread import post_to_gui

        post_to_gui(lambda: fn(*args))
        return False

    def _script_source(self):
        return _BOOTSTRAP % {
            "channeljs": _qwebchannel_js(),
            "names": json.dumps(sorted(self.binds)),
        }

    def _install_script(self):
        core = _modules()[0]
        scripts = self.page.scripts()
        if self._script is not None:
            scripts.remove(self._script)
        s = core.QWebEngineScript()
        s.setName("lunajsobject")
        s.setInjectionPoint(core.QWebEngineScript.InjectionPoint.DocumentCreation)
        s.setWorldId(core.QWebEngineScript.ScriptWorldId.MainWorld)
        s.setRunsOnSubFrames(False)
        s.setSourceCode(self._script_source())
        scripts.insert(s)
        self._script = s

    def _dispatch(self, name, payload):
        func = self.binds.get(name)
        if func is None:
            return
        try:
            args = json.loads(payload) if payload else []
            func(*args)
        except Exception:
            print_exc()

    def _navigating(self, url):
        s = url.toString()
        if s.startswith("data:"):
            s = "about:blank"
        if self.navigating_callback:
            try:
                self.navigating_callback(s, False)
            except Exception:
                print_exc()

    def _load_finished(self, ok):
        # keep the zoom across documents, as WebView2 does
        if not self._dead and abs(self.view.zoomFactor() - self._zoom) > 1e-6:
            self.view.setZoomFactor(self._zoom)

    def _zoom_changed(self, zoom):
        zoom = float(zoom)
        if abs(zoom - self._zoom) < 1e-6:
            return
        self._zoom = zoom
        if self.zoomchange_callback:
            self.zoomchange_callback(zoom)

    def _title_changed(self, title):
        if self.titlechange_callback:
            self.titlechange_callback(title)

    def _icon_changed(self, icon):
        if self.IconChanged_callback and not icon.isNull():
            self.IconChanged_callback(icon)

    def _files_dropped(self, path):
        if self.FilesDropped_callback:
            self.FilesDropped_callback(path)

    def _render_terminated(self, status, code):
        core = _modules()[0]
        if status == core.QWebEnginePage.RenderProcessTerminationStatus.NormalTerminationStatus:
            return
        if self.Crashed_Callback:
            info = "QtWebEngine render process terminated: {} (exit code {})".format(
                getattr(status, "name", status), code
            )
            self.Crashed_Callback(info.encode("utf8"))

    def _view_destroyed(self, *_):
        self._dead = True

    def _context_menu(self, globalpos):
        from PyQt6.QtWidgets import QMenu

        req = self.view.lastContextMenuRequest()
        selected = req.selectedText() if req is not None else ""
        custom = []
        if req is not None:
            core = _modules()[0]
            plain = (
                not req.linkUrl().isValid()
                and req.mediaType() == core.QWebEngineContextMenuRequest.MediaType.MediaTypeNone
                and not req.isContentEditable()
            )
            if selected or plain:
                try:
                    custom = [i for i in (self.on_menu(selected) or []) if i]
                except Exception:
                    print_exc()
        standard = self.view.createStandardContextMenu()
        menu = QMenu(self.view)
        pending = []
        _fill_menu(menu, custom, pending)
        core = _modules()[0]
        WA = core.QWebEnginePage.WebAction
        # saving pages and view-source windows have no handler here
        drop = {self.page.action(WA.SavePage), self.page.action(WA.ViewSource)}
        stdactions = []
        for a in standard.actions():
            if not a.isVisible() or a in drop:
                continue
            if a.isSeparator() and (not stdactions or stdactions[-1].isSeparator()):
                continue
            stdactions.append(a)
        while stdactions and stdactions[-1].isSeparator():
            stdactions.pop()
        if custom and stdactions:
            menu.addSeparator()
        for a in stdactions:
            menu.addAction(a)
        if menu.isEmpty():
            standard.deleteLater()
            menu.deleteLater()
            return
        menu.exec(globalpos)
        menu.deleteLater()
        standard.deleteLater()
        # run the callback after the menu is gone: some callbacks recreate
        # or destroy this very view
        for cb in pending:
            QTimer.singleShot(0, cb)


def _fill_menu(menu, items, pending):
    for item in items:
        if not item or getattr(item, "rawtext", None) in HIDDEN_MENU_TEXTS:
            continue
        if getattr(item, "issep", False):
            menu.addSeparator()
            continue
        sub = getattr(item, "sub", None)
        if sub:
            submenu = menu.addMenu(item.text)
            _fill_menu(submenu, sub, pending)
            continue
        action = menu.addAction(item.text)
        if item.checkable:
            action.setCheckable(True)
            action.setChecked(bool(item.checked))
        if item.clicked:
            action.triggered.connect(
                lambda _=False, cb=item.clicked: pending.append(cb)
            )


class TextBrowserView(object):
    """Last-resort `webview` object (the MSHTML fallback) when QtWebEngine
    is missing: static HTML in a QTextBrowser, no scripting."""

    def __init__(self, host):
        from PyQt6.QtWidgets import QTextBrowser

        self.html_limit = 1 << 60
        self.host = host
        self.html = ""
        self.on_menu = lambda selecttext: []
        self.view = QTextBrowser(host)
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(lambda u: _open_external(u.toString()))
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.setGeometry(host.rect())
        self.view.show()
        self._zoom = 1.0

    def setfocus(self):
        self.view.setFocus()

    def destroy(self):
        pass

    def resize(self, w=None, h=None):
        self.view.setGeometry(self.host.rect())

    def put_PreferredColorScheme(self, darklight):
        pass

    def get_zoom(self):
        return self._zoom

    def set_zoom(self, zoom):
        self._zoom = float(zoom)

    def navigate(self, url):
        q = _to_qurl(url)
        if q.isLocalFile():
            try:
                with open(q.toLocalFile(), "r", encoding="utf8", errors="replace") as f:
                    self.setHtml(f.read())
            except OSError:
                print_exc()
        else:
            _open_external(q.toString())

    def setHtml(self, html):
        self.html = html
        self.view.setHtml(html)

    def bind(self, fname, func):
        pass

    def eval(self, js, callback=None):
        pass

    def _context_menu(self, pos):
        from PyQt6.QtWidgets import QMenu

        selected = self.view.textCursor().selectedText()
        menu = QMenu(self.view)
        pending = []
        try:
            _fill_menu(menu, [i for i in (self.on_menu(selected) or []) if i], pending)
        except Exception:
            print_exc()
        if selected:
            if not menu.isEmpty():
                menu.addSeparator()
            from myutils.config import _TR

            menu.addAction(_TR("复制")).triggered.connect(self.view.copy)
        if not menu.isEmpty():
            menu.exec(self.view.mapToGlobal(pos))
        menu.deleteLater()
        for cb in pending:
            QTimer.singleShot(0, cb)
