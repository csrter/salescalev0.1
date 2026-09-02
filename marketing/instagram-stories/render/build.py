"""
Renders the hiring stories straight to PNG with headless Chromium - no
Photoshop needed. Same copy, palette and safe margins as the .jsx next door.

    CHROME=/path/to/chrome python3 render/build.py

Writes 1080x1920 PNGs into ../out/. Inter is embedded from render/inter.woff2
so output is identical on any machine.
"""
import base64, html, os, pathlib, subprocess

HERE = pathlib.Path(__file__).resolve().parent
OUT  = HERE.parent / "out"
OUT.mkdir(parents=True, exist_ok=True)
CHROME = os.environ.get("CHROME", "chromium")   # any Chrome/Chromium binary

FONT = base64.b64encode((HERE / "inter.woff2").read_bytes()).decode()

BRAND = "Atlas Reach"
C = dict(accent="#2B62E0", lift="#6D95F2", ink="#0A1022", chip="#0F2147", white="#FFFFFF")

SHELL = """<!doctype html><html><head><meta charset=utf-8><style>
@font-face{font-family:Inter;font-weight:100 900;font-style:normal;font-display:block;
  src:url(data:font/woff2;base64,%(font)s) format('woff2');}
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1080px;height:1920px;overflow:hidden}
body{font-family:Inter,'Liberation Sans',Arial,sans-serif;color:%(white)s;
  background:%(ink)s;-webkit-font-smoothing:antialiased}
.slide{position:relative;width:1080px;height:1920px;overflow:hidden;
  background:
    radial-gradient(120%% 70%% at 82%% 8%%, rgba(43,98,224,.30) 0%%, rgba(43,98,224,0) 58%%),
    radial-gradient(95%% 55%% at 10%% 96%%, rgba(43,98,224,.16) 0%%, rgba(43,98,224,0) 60%%),
    linear-gradient(178deg, #101d3d 0%%, %(ink)s 46%%, #070c19 100%%);
  padding:300px 96px;display:flex;align-items:center}
.stack{width:100%%}
.eyebrow{font-weight:700;font-size:30px;letter-spacing:.19em;color:%(lift)s;text-transform:uppercase}
h1{display:inline-block;white-space:nowrap;font-weight:900;letter-spacing:-.035em;line-height:.92}
.pill{display:inline-block;white-space:nowrap;font-weight:800;letter-spacing:-.02em;
  background:%(accent)s;padding:14px 22px;line-height:1.05}
.rule{background:%(accent)s;height:13px;border-radius:1px}
.role{font-weight:500;font-size:40px;line-height:1.3;color:#E8EEFB}
.rows{list-style:none}
.rows li{display:flex;align-items:center;gap:30px;margin-bottom:22px}
.rows i{flex:0 0 16px;width:16px;height:16px;background:%(accent)s}
.rows i.on{background:%(white)s}
.rows span{display:inline-block;white-space:nowrap;font-weight:800;font-size:40px;letter-spacing:-.02em}
.rows span.hl{background:%(accent)s;padding:9px 14px;margin:-9px -14px}
.ctawrap{text-align:center}
.cta{display:block;width:100%%;text-align:center;background:%(accent)s;padding:36px 40px;
  font-weight:900;letter-spacing:-.02em;line-height:1.24}
.cta .chip{background:%(chip)s;padding:8px 14px;margin:-8px 0}
.blk{margin-bottom:34px}
.blk p{white-space:nowrap;font-size:46px;line-height:1.3;font-weight:500;color:#E8EEFB}
.blk p{display:block}
.blk p .hl{font-weight:800;color:%(white)s;background:%(accent)s;
  display:inline-block;padding:10px 14px;margin:0 -14px}
.numrow{display:flex;align-items:center;gap:34px;margin-bottom:56px}
.num{flex:0 0 96px;width:96px;height:96px;border-radius:50%%;background:#142457;
  display:flex;align-items:center;justify-content:center;font-weight:800;font-size:31px;
  letter-spacing:.08em;color:#C9D8F7}
.numrow .t p{white-space:nowrap;font-weight:800;font-size:52px;letter-spacing:-.02em;line-height:1.24}
.numrow .t p{display:block}
.numrow .t p .hl{background:%(accent)s;display:inline-block;padding:12px 16px;margin:0 -16px}
</style></head><body>%(body)s
<script>
function fit(el,min){const box=el.closest('.stack')||el.parentElement;
  const max=box.clientWidth;let s=parseFloat(getComputedStyle(el).fontSize);
  let g=0;while(el.getBoundingClientRect().width>max&&s>min&&g++<400){s-=1;el.style.fontSize=s+'px';}}
document.fonts.ready.then(()=>{
  document.querySelectorAll('[data-fit]').forEach(e=>fit(e,parseFloat(e.dataset.fit)));
  document.documentElement.dataset.ready='1';
});
</script></body></html>"""

def page(body):
    return SHELL % dict(font=FONT, body=body, **C)

def shot(name, body):
    f = HERE / "_tmp.html"
    f.write_text(page(body))
    subprocess.run([CHROME, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                    "--force-device-scale-factor=1", "--window-size=1080,1920",
                    "--virtual-time-budget=8000",
                    "--screenshot=" + str(OUT / (name + ".png")), f.as_uri()],
                   check=True, capture_output=True)
    print("wrote", OUT / (name + ".png"))

E = html.escape

# ---------------------------------------------------------------- solo -----
solo = f"""<div class=slide><div class=stack>
<div class=eyebrow>Now hiring &middot; Cold outreach &middot; US</div>
<h1 data-fit=48 style="font-size:100px;margin:26px 0 30px">Setter seats<br>are open.</h1>
<div style="margin-bottom:44px"><span class=pill data-fit=26 style="font-size:46px">Commission + residuals &middot; OTE $5K&ndash;$7K/mo.</span></div>
<p class=role style="margin-bottom:48px">{BRAND} sells growth systems to home-service
contractors. You run the outreach that books the calls.</p>
<ul class=rows style="margin-bottom:56px">
  <li><i></i><span data-fit=24>Based in the US.</span></li>
  <li><i class=on></i><span class=hl data-fit=24>You&rsquo;ve sold something before.</span></li>
  <li><i></i><span data-fit=24>Cold DMs, calls and texts, daily.</span></li>
  <li><i></i><span data-fit=24>Leads, CRM and scripts provided.</span></li>
</ul>
<div class=ctawrap><span class=cta data-fit=30 style="font-size:58px">DM me for more info!</span></div>
</div></div>"""

# ---------------------------------------------------------------- hook -----
hook = """<div class=slide><div class=stack>
<h1 data-fit=60 style="font-size:132px;margin-bottom:40px">Setter seats<br>are open.</h1>
<div><span class=pill data-fit=30 style="font-size:58px">Commission + residuals &middot; OTE $5K&ndash;$7K/mo.</span></div>
</div></div>"""

# ---------------------------------------------------------------- role -----
role = f"""<div class=slide><div class=stack>
<h1 data-fit=48 style="font-size:108px">The role</h1>
<div class=rule style="width:130px;margin:24px 0 46px"></div>
<div class=blk><p>{BRAND} sells growth systems</p><p>to home-service contractors</p><p>across the US.</p></div>
<div class=blk><p>This is a cold outreach seat.</p><p>You run the outreach that</p>
<p>books the calls &mdash; DMs, calls</p><p>and texts, off lists we provide.</p></div>
<div class=blk><p><span class=hl>Leads, CRM and scripts</span></p><p><span class=hl>come with the seat.</span></p>
<p>You bring the volume.</p></div>
</div></div>"""

# ----------------------------------------------------------------- fit -----
fitslide = """<div class=slide><div class=stack>
<h1 data-fit=48 style="font-size:104px">What we need</h1>
<div class=rule style="width:175px;margin:22px 0 70px"></div>
<div class=numrow><div class=num>01</div><div class=t><p>Based in the US.</p></div></div>
<div class=numrow><div class=num>02</div><div class=t><p><span class=hl>You&rsquo;ve sold</span></p><p><span class=hl>something before.</span></p></div></div>
<div class=numrow><div class=num>03</div><div class=t><p>Cold outreach, weekdays</p><p>10am&ndash;5pm.</p></div></div>
<div class=numrow><div class=num>04</div><div class=t><p><span class=hl>Commission + residuals.</span></p><p>OTE $5K&ndash;$7K/mo.</p></div></div>
</div></div>"""

# ------------------------------------------------------------------ cta ----
cta = """<div class=slide><div class=stack>
<h1 data-fit=60 style="font-size:132px">We interview<br>same day.</h1>
<div class=rule style="width:420px;height:14px;margin:20px 0 90px"></div>
<div class=ctawrap><span class=cta data-fit=34 style="font-size:64px">DM me for more info!</span></div>
</div></div>"""

for n, b in [("setter-hiring", solo), ("01-hook", hook), ("02-role", role),
             ("03-fit", fitslide), ("04-cta", cta)]:
    shot(n, b)
