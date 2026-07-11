"""Shared post-OAuth landing for the ad-platform connect callbacks.

On the hosted web app a successful callback redirects back into the SPA. In
the desktop app the UI is file:// and OAuth runs in the system browser, so
there's nothing web-hosted to redirect to — instead we serve a small
self-contained page telling the user to return to the app, which refreshes
its connections on window focus.

Errors (user canceled the dialog, platform rejected the exchange) always
render the standalone page, on web too: the SPA has no route that could
display them, and a silent redirect would look like success.
"""

import html

from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import get_settings

# Brand colors mirror the app logo (see frontend logo.tsx / App.css).
_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ — Salescale</title>
<style>
  html,body{height:100%;margin:0}
  body{display:flex;align-items:center;justify-content:center;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;
    background:#0f1a33;color:#eaf0ff}
  .card{max-width:440px;padding:40px;text-align:center}
  .mark{width:56px;height:56px;border-radius:14px;background:#0f2147;
    display:inline-flex;align-items:center;justify-content:center;margin-bottom:20px}
  .icon{width:30px;height:30px;border-radius:50%;background:__ICON_BG__;color:#fff;
    display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:700}
  h1{font-size:1.35rem;margin:0 0 8px}
  p{color:#9fb0d0;line-height:1.5;margin:0 0 6px}
  .platform{color:#2b62e0;font-weight:600}
  .detail{font-size:.9rem;color:#7d8fb5}
</style>
</head>
<body>
  <div class="card">
    <div class="mark"><div class="icon">__ICON__</div></div>
    <h1><span class="platform">__PLATFORM__</span> __TITLE__</h1>
    __BODY__
  </div>
</body>
</html>"""

_SUCCESS_BODY = (
    "<p>You can close this tab and return to the Salescale app &mdash; your new "
    "connection will appear automatically.</p>"
)

# An agency login sees many ad accounts (a Google MCC's roster, a Meta
# Business Manager's client list) — nothing is attached automatically in that
# case; the Admin assigns accounts to clients in the app.
_SELECT_BODY = (
    "<p>The connection works and can see several ad accounts.</p>"
    "<p>Return to the Salescale app and use <strong>Manage accounts</strong> on "
    "this client to choose which accounts belong to it.</p>"
)


def _page(platform: str, title: str, icon: str, icon_bg: str, body: str) -> HTMLResponse:
    page = (
        _PAGE_HTML.replace("__PLATFORM__", html.escape(platform.title()))
        .replace("__TITLE__", html.escape(title))
        .replace("__ICON__", icon)
        .replace("__ICON_BG__", icon_bg)
        .replace("__BODY__", body)
    )
    return HTMLResponse(page)


def post_connect_response(
    client_id: str,
    platform: str,
    select_accounts: bool = False,
    error: str | None = None,
):
    """Land the user after the OAuth callback.

    - error → standalone error page (web and desktop) with the platform's
      actual message, so a failed connect is diagnosable instead of a 500.
    - select_accounts → success page/redirect that points the Admin at the
      account picker (several accounts were discoverable, none auto-attached).
    - plain success → redirect into the SPA on web; "return to the app" page
      on desktop (DESKTOP_MODE), where there is no hosted frontend to land on.
    """
    settings = get_settings()
    if error is not None:
        body = (
            f"<p>{html.escape(error)}</p>"
            "<p class=\"detail\">Nothing was connected. You can close this tab, "
            "return to the Salescale app, and try again.</p>"
        )
        return _page(platform, "connection failed", "&#33;", "#d0455a", body)
    if settings.desktop_mode:
        body = _SELECT_BODY if select_accounts else _SUCCESS_BODY
        return _page(platform, "connected", "&#10003;", "#2b62e0", body)
    suffix = "&select_accounts=1" if select_accounts else ""
    return RedirectResponse(
        f"{settings.frontend_origin}/clients/{client_id}?connected={platform}{suffix}"
    )
