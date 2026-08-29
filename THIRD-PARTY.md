# 第三方组件与许可

本项目自身使用 MIT（见 `LICENSE`）。下面是随发布物一起分发、或在运行时下载的第三方组件。
两种发布物的义务不同，所以分开列：**桌面版 zip** 是二进制分发，**容器镜像**也是；克隆仓库自己
跑则只是使用，不触发分发义务。

## 一、随二进制分发的代码

### `panel/vendor/utils/` —— 上游 cloakbrowser 辅助函数，BSD 2-Clause

作者 Milly，来自 https://github.com/Milly/anyrouter-check-in 。本项目从中复制了 5 个文件
（1061 行）：`browser.py`、`popups.py` 及其依赖的 `debug.py`、`proxy.py`、`__init__.py`。
`panel/browser_login.py` 从中导入 6 个名字，只在**浏览器登录**时用到；HTTP 签到主路径完全
不经过它。上游项目的其余部分（签到脚本本体、workflow、它自己的测试与配置）都不在本仓库里。

唯一的改动：`browser.py` 里三条内部 import 改成了相对导入。详见 `panel/vendor/README.md`。

BSD-2 第 2 条要求二进制分发复制其版权声明，所以声明随代码走：

- 仓库里在 `panel/vendor/LICENSE`
- 桌面版 exe 里在 `_internal/panel/vendor/LICENSE`
- 容器镜像里在 `/app/panel/vendor/LICENSE`

exe 里那一份靠 `desktop/desktop.spec` 的 `datas` 显式带上（LICENSE 不是代码，依赖图收不到它）；
镜像里那一份跟着 `COPY panel/` 一起进去。改这两处时不要把它漏掉。

### `pystray` 0.19.5 —— **LGPLv3**，需要注意

托盘图标。它被打进了桌面版 exe 的 PYZ 归档里（实测：exe 二进制中出现 13 次），也就是说
**桌面版 zip 是在二进制分发 LGPLv3 代码**。这是本项目唯一带传染性条款的依赖。

LGPL 的要求不是「你的代码也得开源」——本项目本来就是开源的——而是使用者要能**替换掉这个库**。
对 PyInstaller 这种打包形式，通常的做法是：

1. 声明它的许可和版本（本文件即是）；
2. 指明获取源码的位置：<https://github.com/moses-palmer/pystray>；
3. 说明替换方式：本项目从源码运行时 `pip install pystray==<你的版本>` 即可换成任意版本，
   `desktop/__main__.py` 只用 `Icon` / `Menu` / `MenuItem` 三个公开接口，没有改动过这个库。

只跑 `run.py` 或容器的人完全不涉及这一条 —— `pystray` 只在 `requirements/desktop.txt` 里，
容器镜像不含它。

如果你希望桌面版彻底不带 LGPL 组件，只有换掉托盘实现这一条路（例如自己用 `Shell_NotifyIconW`
写一个），那是一次真实的改动，不是改一行声明。

### 其他随发布物分发的依赖

均为宽松许可，义务限于保留声明：

| 组件 | 版本 | 许可 |
|---|---|---|
| fastapi | 0.141.1 | MIT |
| uvicorn | 0.52.2 | BSD-3-Clause |
| starlette | 1.6.0 | BSD-3-Clause |
| pydantic | 2.13.4 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| h11 | 0.16.0 | MIT |
| anyio | 4.14.2 | MIT |
| cloakbrowser（Python 包） | 0.5.7 | MIT |
| playwright（Python 包） | 1.62.0 | Apache-2.0 |
| cryptography | 50.0.0 | Apache-2.0 或 BSD-3-Clause |
| PyNaCl | 1.6.2 | Apache-2.0 |
| pywebview | 6.2.1 | BSD-3-Clause |
| pillow | 12.3.0 | MIT-CMU |
| pythonnet / clr-loader | 3.1.0 | MIT |
| greenlet | 3.5.5 | MIT 与 PSF-2.0 |
| certifi | 2026.7.22 | MPL-2.0（未修改，file-level copyleft 因此已满足） |

前端依赖（React、HeroUI、Vite、Tailwind）编译进 `frontend/dist/` 的静态文件，均为 MIT。

## 二、打包工具

**PyInstaller 6.22.2 —— GPLv2，但不影响你的发布物。** 它的许可带有明确的例外条款，允许用它
打包出的程序按任何许可分发，包括闭源。所以桌面版 zip 不因为用了 PyInstaller 而需要 GPL 化。

## 三、运行时下载、不随发布物分发的组件

### CloakBrowser 浏览器内核

`requirements/browser.txt` 里的 `cloakbrowser` Python 包是 MIT，但它驱动的**浏览器可执行文件
是另一个东西**：一个约 500MB 的闭源产品，由 `panel.sandbox.ensure_chromium()` 在首次需要浏览器
时从 <https://cloakbrowser.dev> / CloakHQ 的 GitHub Releases 下载到 `.local/cloakbrowser/`。

对使用者意味着三件事，都应当知情：

- **它不在本仓库里，也不在 zip 和镜像里。** 第一次做浏览器登录时才下载，期间界面看起来像卡住。
  纯 HTTP 签到的账号在它下载完成前就能正常工作。
- **免费档足够本项目使用。** 实测：未设置任何 `CLOAKBROWSER_LICENSE_KEY`、`~/.cloakbrowser/`
  下没有 `.license_cache`，浏览器登录路径正常工作。它有付费的 Pro 档（会话数上限等），本项目
  不需要。
- **下载和许可校验是对第三方服务器的请求。** 这是本项目除促销卡清单之外唯一的外发连接，且它由
  cloakbrowser 自己发起、不受 `PANEL_PROMO=0` 控制。不想要它就不要用浏览器登录：只用密码登录的
  账号全程不碰浏览器（见 README「服务器上怎么做浏览器登录」一节的第一条建议）。

### 促销卡清单

`panel/promo.py` 每 5 分钟向作者的另一个公开仓库拉一份静态 `promos.json`。请求不带 query、
body、cookie，也不带任何关于你的信息（`panel/tests/test_promo.py::test_the_request_carries_nothing_but_the_url`
就是断言这一点）。`PANEL_PROMO=0` 完全关闭，什么都不再请求。详见 `docs/promo-cards.md`。

## 四、这个项目签到的目标站点

本项目不隶属于、也未被 anyrouter.top、agentrouter.org、seekai.cc、sotamodel.net 或任何
New API 分站背书。站点名称出现在代码和文档里，仅因为它们的行为差异是被实测记录下来的
（见 `docs/adr/`）。各站点的服务条款由使用者自行遵守。
