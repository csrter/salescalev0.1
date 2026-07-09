"""Shared post-OAuth landing for the ad-platform connect callbacks.

On the hosted web app the callback redirects back into the SPA. In the desktop
app the UI is file:// and OAuth runs in the system browser, so there's nothing
web-hosted to redirect to — instead we serve a small self-contained success
page telling the user to return to the app, which refreshes its connections on
window focus.
"""

from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import get_settings

# Brand colors mirror the app logo (see frontend logo.tsx / App.css).
_SUCCESS_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connected — Salescale</title>
<style>
  html,body{height:100%;margin:0}
  body{display:flex;align-items:center;justify-content:center;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;
    background:#0f1a33;color:#eaf0ff}
  .card{max-width:420px;padding:40px;text-align:center}
  .mark{width:56px;height:56px;border-radius:14px;background:#0f2147;
    display:inline-flex;align-items:center;justify-content:center;margin-bottom:20px}
  .check{width:30px;height:30px;border-radius:50%;background:#2b62e0;color:#fff;
    display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:700}
  h1{font-size:1.35rem;margin:0 0 8px}
  p{color:#9fb0d0;line-height:1.5;margin:0}
  .platform{color:#2b62e0;font-weight:600}
</style>
</head>
<body>
  <div class="card">
    <div class="mark"><div class="check">&#10003;</div></div>
    <h1><span class="platform">__PLATFORM__</span> connected</h1>
    <p>You can close this tab and return to the Salescale app &mdash; your new
    connection will appear automatically.</p>
  </div>
</body>
</html>"""


def post_connect_response(client_id: str, platform: str):
    """Redirect into the SPA on web; serve a "return to the app" page on
    desktop (DESKTOP_MODE), where there is no hosted frontend to land on."""
    settings = get_settings()
    if settings.desktop_mode:
        return HTMLResponse(_SUCCESS_HTML.replace("__PLATFORM__", platform.title()))
    return RedirectResponse(
        f"{settings.frontend_origin}/clients/{client_id}?connected={platform}"
    )
