"""Linux web views: route gui.usefulwidget's WebView2/EdgeHTML/MSHTML widgets
to QtWebEngine (lunaplat.linux.qt.webengine) and hide the WebView2-only
settings (browser extensions, runtime download hints).

The widget classes stay the same objects (isinstance checks and subclasses
such as the main window renderer and the history view keep working); only
the methods that create or talk to the native web view are replaced.
"""

import functools
from traceback import print_exc

from lunaplat.linux.qt import webengine

EXT_TEXTS = ("附加浏览器插件", "浏览器插件")


def _darklight(mod):
    try:
        return int(mod.ui_settings.get("darklight2", 0))
    except Exception:
        return 0


def patch(mod):
    """gui.usefulwidget"""
    WebviewWidget = mod.WebviewWidget
    EdgeHtmlWidget = mod.EdgeHtmlWidget
    MSHtmlWidget = mod.MSHtmlWidget
    AbstractWebviewWidget = mod.AbstractWebviewWidget
    auto_select_webview = mod.auto_select_webview

    def webview_init(self, parent=None, transp=False, loadext=False):
        AbstractWebviewWidget.__init__(self, parent)
        self.url = ""
        self.crashedsignal.connect(self._WebviewWidget___crashed_handle)
        self.webview = webengine.WebEngineView(
            self, transp=transp, darklight=_darklight(mod)
        )
        self.webview.on_menu = lambda c: self.on_menu(c)
        self.destroyed.connect(self.webview.destroy)
        self.webview.set_callbacks(
            self.on_ZoomFactorChanged.emit,
            self._WebviewWidget__on_load,
            self.dropfilecallback.emit,
            self.titlechanged.emit,
            self.IconChanged.emit,
            self.crashedsignal.emit,
        )

    def showError(e):
        mod.RichMessageBox(
            mod.gobject.base.focusWindow,
            mod._TR("错误"),
            "{}\n\nQtWebEngine (PyQt6-WebEngine) is required for web views.".format(
                e
            ),
        )

    def edgehtml_init(self, parent=None, transp=False):
        # Only reached when QtWebEngine failed for WebviewWidget.
        raise RuntimeError("EdgeHTML is not available on Linux")

    def mshtml_init(self, parent=None):
        AbstractWebviewWidget.__init__(self, parent)
        self.webview = webengine.TextBrowserView(self)
        self.webview.on_menu = lambda c: self.on_menu(c)
        self.curr_url = None

    def mshtml_getHtml(self, elementid):
        return self.webview.html

    def createwebview(self, shoudong=False, transp=False):
        # No WebviewWidget_for_auto: it only adds WebView2 extension menus.
        try:
            browser = mod.WebviewWidget(transp=transp)
        except Exception as e:
            print_exc()
            if shoudong:
                mod.WebviewWidget.showError(e)
            try:
                browser = mod.EdgeHtmlWidget(self, transp=transp)
            except Exception:
                browser = mod.MSHtmlWidget(self)
        browser.on_menu = self._auto_select_webview__on_menu
        return browser

    def Exteditor(parent=None, *_, **__):
        mod.RichMessageBox(
            parent,
            mod._TR("错误"),
            "Browser extensions (WebView2) are not supported on Linux.",
        )

    WebviewWidget.__init__ = webview_init
    WebviewWidget.showError = staticmethod(showError)
    EdgeHtmlWidget.__init__ = edgehtml_init
    MSHtmlWidget.__init__ = mshtml_init
    MSHtmlWidget.getHtml = mshtml_getHtml
    auto_select_webview._createwebview = createwebview
    mod.Exteditor = Exteditor
    mod.ExtensionSetting = lambda *a, **k: None


def patch_config_strings(mod):
    """UI strings name WebView2 ("使用Webview2显示", crash messages...); on
    Linux the engine is QtWebEngine. _TR looks ___TR up at call time, so
    wrapping it covers every caller."""
    orig = getattr(mod, "___TR", None)
    if orig is None:
        return

    def ___TR(k):
        r = orig(k)
        if isinstance(r, str) and ("ebview2" in r or "ebView2" in r):
            r = r.replace("WebView2", "QtWebEngine").replace("Webview2", "QtWebEngine")
        return r

    mod.___TR = ___TR


def patch_display_text(mod):
    """gui.setting.display_text: hide the WebView2 browser-extension row and
    name the engine after what renders it."""
    orig_reset = mod.resetgroudswitchcallback
    orig_combo = mod._createseletengeinecombo

    @functools.wraps(orig_reset)
    def resetgroudswitchcallback(self, group):
        orig_reset(self, group)
        try:
            _hide_extension_rows(mod, self.engineexpander)
        except Exception:
            print_exc()

    @functools.wraps(orig_combo)
    def _createseletengeinecombo(self):
        combo = orig_combo(self)
        try:
            for i in range(combo.count()):
                if combo.itemText(i) == "Webview2":
                    combo.mo.item(i).setText("QtWebEngine")
        except Exception:
            print_exc()
        return combo

    mod.resetgroudswitchcallback = resetgroudswitchcallback
    mod._createseletengeinecombo = _createseletengeinecombo


def _hide_extension_rows(mod, expander):
    from qtsymbols import QLabel

    texts = {mod._TR(t) for t in EXT_TEXTS} | set(EXT_TEXTS)
    for w in expander.contentWidgets():
        labels = [w] if isinstance(w, QLabel) else []
        labels += w.findChildren(QLabel)
        if any(l.text() in texts for l in labels):
            expander.removeContentWidget(w)
