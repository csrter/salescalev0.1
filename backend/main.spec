# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('alembic', 'alembic'), ('alembic.ini', '.')]
binaries = []
hiddenimports = ['email_validator']
hiddenimports += collect_submodules('app')
tmp_ret = collect_all('psycopg')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('uvicorn')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('alembic')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('mako')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# The AI provider SDKs are imported lazily inside services/ai_provider.py
# functions, so PyInstaller's static analysis never sees them — without these
# the packaged app has NO AI features (insights, research, personalization).
for _ai_pkg in ('anthropic', 'openai', 'google.genai'):
    tmp_ret = collect_all(_ai_pkg)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# Starlette imports this lazily in request.form() (Twilio webhook parsing).
hiddenimports += ['multipart']
# google-ads loads its versioned service/enum/type modules DYNAMICALLY inside
# GoogleAdsClient.get_service(), so static analysis misses them — without this
# every Google Ads call in the packaged app fails with the library's
# (client-side) "Specified service X does not exist in Google Ads API v24".
# Collect the default version's whole subpackage; bump alongside library
# upgrades (google.ads.googleads.client._DEFAULT_VERSION is the authority).
hiddenimports += collect_submodules('google.ads.googleads.v24')


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='main',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
