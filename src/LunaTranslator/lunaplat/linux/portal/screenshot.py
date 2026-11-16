"""org.freedesktop.portal.Screenshot: whole-screen capture to a file."""

from urllib.parse import unquote, urlparse

from lunaplat.linux import portal
from lunaplat.linux.qtthread import run_in_gui


def _take():
    token = portal.new_token()
    res = portal.call_with_response(
        "org.freedesktop.portal.Screenshot",
        "Screenshot",
        ["", {"handle_token": token, "interactive": False, "modal": False}],
    )
    if not res or res[0] != 0:
        return None
    uri = res[1].get("uri") if isinstance(res[1], dict) else None
    if not uri:
        return None
    return unquote(urlparse(str(uri)).path)


def take():
    """Path of a temporary PNG of the whole desktop, or None."""
    return run_in_gui(_take, timeout=70, default=None)
