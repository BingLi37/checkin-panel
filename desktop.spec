# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build of the desktop way to run the panel (ADR-0016).

	.venv\\Scripts\\pyinstaller.exe desktop.spec

Produces `dist/签到面板/签到面板.exe`. Three decisions worth the words:

**onedir, not onefile.** onefile unpacks ~40MB to a temp directory on every launch and
deletes it on exit, so startup is slower and `sys._MEIPASS` moves each run. It also cannot
hold the writable folders: `data/panel.db` has to live next to the exe or a user's accounts
vanish (`sandbox.roots` draws that line).

**No browser in the bundle.** `.local/cloakbrowser` is ~500MB and version-specific;
`sandbox.ensure_chromium()` fetches it on first run into the folder beside the exe, which
keeps the build small and lets the browser update without a rebuild (ADR-0006).

**A v6 manifest, explicitly.** `desktop_dialog` calls `TaskDialogIndirect`, which exists
only in Common Controls v6 — `System32\\comctl32.dll` is v5 and does not export it, so
without the dependency below the close dialog fails at attribute lookup and the X button
would quit outright. PyInstaller's default bootloader manifest does not declare it.
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve()

# PyInstaller execs this file with the project root off sys.path, so the icon module it
# shares with the app is not importable until we put it there.
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))

import desktop_icon

# Generated, not committed: one geometry shared with the SPA's favicon (see desktop_icon).
ICON = desktop_icon.write_ico(ROOT / 'build' / 'desktop.ico')

# Read-only, resolved against sys._MEIPASS at runtime. The SPA is required — without
# frontend/dist the window has nothing to show. The vendored helpers are imported by
# panel.browser_login through sys.path, so they ship as data, not as a package.
datas = [
	(str(ROOT / 'frontend' / 'dist'), 'frontend/dist'),
	(str(ROOT / 'anyrouter-check-in' / 'utils'), 'anyrouter-check-in/utils'),
	# Upstream's BSD-2 clause 2 asks that a binary redistribution reproduce its notice, and
	# this build is one: the cloakbrowser helpers above are its code. Ships next to them, so
	# a reader who finds the code finds the terms it came under.
	(str(ROOT / 'anyrouter-check-in' / 'LICENSE'), 'anyrouter-check-in'),
]

# Imported by name or through sys.path, so the dependency graph cannot see them.
hiddenimports = [
	'utils.browser',
	'utils.popups',
	# uvicorn resolves its loop/protocol implementations from strings at runtime.
	'uvicorn.loops.asyncio',
	'uvicorn.protocols.http.h11_impl',
	'uvicorn.protocols.websockets.websockets_impl',
	'uvicorn.lifespan.on',
]

a = Analysis(
	['desktop.py'],
	pathex=[str(ROOT), str(ROOT / 'anyrouter-check-in')],
	binaries=[],
	datas=datas,
	hiddenimports=hiddenimports,
	hookspath=[],
	runtime_hooks=[],
	# The browser stack is reached only through cloakbrowser at runtime and drags in a large
	# tree; excluding it here would break the browser login, so it is deliberately kept.
	excludes=['tkinter', 'pytest', 'matplotlib', 'numpy'],
	noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
	pyz,
	a.scripts,
	[],
	exclude_binaries=True,
	name='签到面板',
	debug=False,
	bootloader_ignore_signals=False,
	strip=False,
	upx=False,
	console=False,  # --windowed: no console window behind the panel
	disable_windowed_traceback=False,
	icon=str(ICON),
	manifest="""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls"
        version="6.0.0.0" processorArchitecture="*" publicKeyToken="6595b64144ccf1df"
        language="*" />
    </dependentAssembly>
  </dependency>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
    </windowsSettings>
  </application>
</assembly>
""",
)

coll = COLLECT(
	exe,
	a.binaries,
	a.datas,
	strip=False,
	upx=False,
	name='签到面板',
)

# Anything in `datas` lands under `_internal/`, which is the right place for things the code
# reads and the wrong place for things a *person* needs. Whoever downloads the zip has only the
# zip — no repo, no README — so the licence and a short first-run note go beside the exe, where
# they are the first thing in the folder. Copied after COLLECT because that is when the folder
# exists; a spec is just Python.
import shutil

_BUNDLE = Path(DISTPATH) / '签到面板'
for src, dst in (
	(ROOT / 'LICENSE', 'LICENSE.txt'),
	(ROOT / 'THIRD-PARTY.md', 'THIRD-PARTY.md'),
):
	if src.exists():
		shutil.copy2(src, _BUNDLE / dst)

# Deliberately not the README: that one is written for someone holding the repo (venv, npm,
# docker compose). A downloader needs the four things that actually differ for them.
# utf-8-sig, not utf-8: this is a .txt a stranger opens by double-clicking, and Notepad on an
# older Windows guesses the ANSI code page without a BOM — which turns the whole note into
# mojibake, i.e. the one file meant to explain things becomes the first thing that looks broken.
(_BUNDLE / '使用说明.txt').write_text(
	'''自动签到面板
============

启动
----
双击「签到面板.exe」。浏览器会自动打开面板界面。

首次运行会遇到的三件事，都不是故障：

1. Windows 弹出「已阻止不受信任的应用」
   这个程序没有代码签名证书。点「更多信息」->「仍要运行」。

2. 杀毒软件报毒
   PyInstaller 打包的程序常被误报。需要加白名单。

3. 第一次用「浏览器登录」时界面像卡住了
   它在下载约 500MB 的浏览器内核，请等它下完。
   只用密码登录的账号不需要等，可以直接签到。

关窗口不会退出
--------------
点 X 会问一次，然后收进右下角通知区域，继续每天自动签到。
左键点托盘图标召回窗口，右键选「退出」才真的停止。

你的数据在哪
------------
就在这个文件夹里:
  data\\panel.db          账号和密码
  .browser_profiles\\     浏览器登录状态
  .local\\cloakbrowser\\  下载的浏览器内核

整个文件夹可以直接复制搬走，账号跟着走。

重要:
  data\\panel.db 里的密码是明文保存的，请当作密码文件对待。
  面板本身没有登录密码 —— 它默认只监听 127.0.0.1，也就是只有这台
  电脑能访问。不要把它暴露到公网，否则任何人都能读到你所有账号的
  密码。需要远程访问请看项目文档 docs/deploying.md。

许可
----
本程序 MIT 许可，见 LICENSE.txt。
第三方组件的许可见 THIRD-PARTY.md。
''',
	encoding='utf-8-sig',
)
