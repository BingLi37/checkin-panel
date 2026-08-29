# 自动签到面板

给 New API 类型的中转站做每日签到的自建面板。加账号、看余额、每天自动领，一台机器上跑，不依赖
任何外部服务。

优先走 HTTP 协议签到，只有站点确实拦得住协议（OAuth 会话过期、Turnstile、WAF）才启动浏览器 ——
所以绝大多数账号的日常签到只是几个 HTTP 请求，快且不吃资源。

已实测适配的站点行为差异记录在 `docs/adr/`：anyrouter.top、agentrouter.org、seekai.cc、
sotamodel.net 各有各的坑，代码里对应的判断都能追到一条 ADR。

## 先读这一段，它决定你怎么部署

**面板没有登录。** 任何能访问到它端口的人，都能从 `GET /api/accounts` 拿到**每个账号的明文密码
和 session**，这是设计如此（ADR-0003：一个单用户、跑在自己机器上的面板）。

所以：

- 自己电脑上用 → 默认绑 `127.0.0.1`，没问题，跳过这段。
- 想从手机/别的设备访问 → 用 Tailscale、WireGuard 之类的私有网络，**不要**把端口公开。
- 必须放公网 → 认证 + TLS + 面板自己的端口不对外，三者缺一不可。做法见
  [`docs/deploying.md`](docs/deploying.md)。

`data/panel.db` 是账号的唯一副本，请按密码文件对待：单独备份，不要提交，不要打进镜像。

## 三种运行方式

| | 适合谁 | 你得到 | 你接受 |
|---|---|---|---|
| **桌面版** | 自己电脑上用的人 | 双击就开，关窗口继续后台签到，托盘图标 | 仅 Windows |
| **容器** | 部署在服务器上的人 | 重启不丢，Docker 能跑的地方都能跑 | 需要 Docker，且要正确处理暴露 |
| **控制台** | 部署在服务器上的人 / 想改代码的人 | 装完就能跑 | 终端窗口得一直开着 |

**三种不能同时开。** 它们共用 `data/panel.db` 和 `.browser_profiles/`，同时开会锁数据库、两个
浏览器抢同一个 profile。桌面版自己会拒绝启动第二个实例；容器用的是独立卷，所以它跟另外两种同时
开不会报错，而是变成**两套不同的账号往同一批站点签到**，更难发现。

### 桌面版

不想装 Python 的话，去 Releases 下载 zip，解压，双击 `签到面板.exe`。

从源码跑或自己打包 —— 两者都要求[界面已经构建过](#从源码安装)一次（`frontend/dist/`），
否则 `desktop/desktop.spec` 会直接报 `Unable to find ...frontend\dist`：

```bat
.venv\Scripts\python.exe -m desktop                :: 直接运行
.venv\Scripts\pyinstaller.exe desktop\desktop.spec :: 打包到 dist\签到面板\
```

点 **X 不会退出** —— 它会问一次（可以勾「不再提示」），然后收进通知区域。左键点托盘图标召回窗口，
右键选**退出**才真的停。这就是桌面版存在的意义：不占着一个窗口也能继续每天签到。

`dist\签到面板\` 整个文件夹可以随便搬。`data\`、`.browser_profiles\`、`.local\cloakbrowser\`
都建在 exe 旁边，所以账号跟着文件夹走。默认绑 `127.0.0.1`。

**首次使用注意三件事**（不是 bug）：

- Windows 会弹 SmartScreen「已阻止不受信任的应用」—— exe 没有代码签名证书。点「更多信息」→
  「仍要运行」。
- 部分杀毒软件会误报 PyInstaller 打出来的 exe，需要加白名单。
- 第一次做**浏览器登录**时要下载约 500MB 的浏览器内核，界面看着像卡住，实际在下载。纯 HTTP
  签到的账号不用等它。

### 容器

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

然后打开 <http://127.0.0.1:8000>。第一次签到前先把时区设对，在 `docker-compose.yml` 里：

```yaml
environment:
  TZ: Asia/Shanghai
```

这个不是装饰。站点在特定时刻才开放当天奖励（账号上的 `checkin_after`），而面板按**本地**时间
算一天，容器留在 UTC 会在错误的时刻反复重试。

`docker-compose.yml` 里发布的是 `127.0.0.1:8000:8000`，只有本机能访问 —— 要改成对外之前，先读
上面那段和 `docs/deploying.md`。

### 控制台

```bat
start.bat
```

或者 `.venv\Scripts\python.exe run.py`。窗口关掉调度器就停了，没有任何提示。

注意 `run.py` 默认绑 `0.0.0.0`（局域网可达），`start.bat` 会替你改成 `127.0.0.1`。直接跑
`run.py` 的话自己设 `PANEL_HOST`。

## 从源码安装

需要 Python 3.11+（本项目实测在 3.14），以及 Node 20+（仅在你要改前端时）。

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements\browser.txt
```

四个 requirements 文件是分层的，按需装：

| 文件 | 内容 |
|---|---|
| `requirements/base.txt` | 面板本体，只能做 HTTP 签到 |
| `requirements/browser.txt` | 加浏览器（OAuth 重登录、`visit` 站点、Turnstile）—— **大多数人要这个** |
| `requirements/desktop.txt` | 加窗口、托盘、打包器 |
| `requirements/dev.txt` | 加 pytest |

界面需要自己构建一次 —— `frontend/dist/` 是构建产物，不在仓库里（容器方式不用管，镜像自己会构建）：

```bash
cd frontend
npm ci --legacy-peer-deps
npm run build
```

`--legacy-peer-deps` 是必需的：`@heroui/theme` 声明 peer 依赖 `tailwindcss>=4`，而项目用的是
`3.4.x`。这是个已知的、待解决的前端依赖冲突，不影响构建结果。

没有 `frontend/dist/` 时面板只提供 API，不提供界面。

## 服务器上怎么做浏览器登录

这是部署到服务器后唯一会卡住的地方，单独讲。

面板日常自动签到走的是**无头**浏览器，服务器上本来就没问题。但界面上那个「浏览器登录」按钮默认
开一个**可见窗口** —— 窗口开在跑面板的那台机器上，也就是服务器，你看不见；容器里更彻底，镜像里
没有 X 显示服务。

好消息是：**人只在「首次授权」时被需要一次**，而且只对一类账号。

### 先判断你是否真的需要它

| 账号情况 | 需要人工吗 |
|---|---|
| 能设密码的站点 | **不需要。** 会话过期时无头浏览器自己用密码重登录 |
| OAuth-only（LinuxDO / GitHub，站点不给绑密码） | 需要，但只在 IdP 会话失效时，通常数周到数月一次 |

**所以第一条建议是：能绑密码的就绑密码。** 这不是绕过问题，是让问题不存在 —— 密码账号的日常
签到完全不碰浏览器。添加账号时如果站点允许设密码，面板会提示「能设密码就转成纯 HTTP」。

对 OAuth-only 身份和 anyrouter.top（WAF + `visit` 机制，每次都要浏览器）无效，那些走下面。

### 方案 A：容器里临时开一个 VNC，看着窗口点（推荐）

镜像里**已经有 `Xvfb`**（`playwright install-deps` 顺带装的），缺的只是一个能看见它的桥。

在 `docker-compose.yml` 里加一个按需启动的服务。注意 `profiles:` 让它默认不启动：

```yaml
services:
  panel:
    environment:
      DISPLAY: ":99"          # 让面板的浏览器开在虚拟显示上

  vnc:
    profiles: ["vnc"]         # 默认不起，只在需要授权时起
    image: anyrouter-checkin-panel
    container_name: checkin-vnc
    network_mode: "service:panel"
    volumes:
      - panel-profiles:/app/.browser_profiles
    user: root
    entrypoint: >
      sh -c "apt-get update && apt-get install -y --no-install-recommends x11vnc websockify novnc &&
             Xvfb :99 -screen 0 1280x800x24 &
             sleep 2 &&
             x11vnc -display :99 -forever -localhost -nopw &
             websockify --web=/usr/share/novnc 127.0.0.1:6080 127.0.0.1:5900"
```

用的时候：

```bash
docker compose --profile vnc up -d vnc          # 需要授权时才起
ssh -L 6080:127.0.0.1:6080 you@your-server      # 从本机开隧道
# 浏览器打开 http://127.0.0.1:6080/vnc.html，然后在面板里点「浏览器登录」
docker compose --profile vnc down               # 授权完就关掉
```

**两条不能松的要求：**

1. **VNC 端口只走 SSH 隧道或私有网络，绝不发布到公网。** 上面 `x11vnc -localhost` 和
   `websockify 127.0.0.1` 都是为此 —— 一个已登录 IdP 的远程桌面比面板本身更值钱。
2. **用完就关。** 它不该常驻。

坦白一句：我没有实测过有头 Chromium 在 Xvfb 下真能起来（要先下 500MB 内核）。这是 Xvfb 的本职
工作、按理可行，但这是推断不是测量结果，你第一次跑可能需要调整。

### 方案 B：SSH X11 转发

```bash
ssh -X you@your-server
# 容器里：docker exec -e DISPLAY=$DISPLAY -it checkin-panel ...
```

窗口直接开在你自己屏幕上，服务器上不留任何常驻暴露。代价是本机要有 X 服务器（Windows 上要装
VcXsrv 一类），容器场景还要把 `DISPLAY` 和 X socket 传进去，比方案 A 绕。

### 一条走不通的路

**别想着「在本地授权好再把 profile 拷到服务器」。** Windows 的 profile 里
`Local State` 有 `os_crypt.encrypted_key`，那是 DPAPI 加密、绑当前 Windows 账户的，搬到 Linux
解不开 cookie。数据库里的 `session` 字段可以搬（纯字符串），但 `visit` 类账号的站点会话在
profile 里，搬不动。

## 环境变量

全部可选。

| 变量 | 默认 | 说明 |
|---|---|---|
| `PANEL_HOST` | `run.py`: `0.0.0.0` / 桌面版: `127.0.0.1` | 绑定地址。**这是信任边界** |
| `PANEL_PORT` | `8000` | 端口 |
| `PANEL_SCHEDULER` | 开 | `0` 关掉每日自动签到 |
| `PANEL_PROMO` | 开 | `0` 关掉推荐卡片，不再发起任何请求 |
| `CHECKIN_PROXY_URL` | `http://127.0.0.1:7897` | 仅浏览器登录用。容器里主机代理是 `http://host.docker.internal:7897` |
| `TZ` | 系统 | 容器里必须设对，见上文 |

## 开发

```bat
.venv\Scripts\python.exe -m pytest              :: 201 个测试
cd frontend && npm run dev                       :: 前端热重载，:5173 代理到 :8000
```

Fork 之后建议先装上这个钩子，它会在 `git commit` 时拦下形似凭据的字符串和数据库文件：

```bat
.venv\Scripts\python.exe check_secrets.py --install
.venv\Scripts\python.exe check_secrets.py --all   :: 或手动全树扫一遍
```

它只匹配有固定前缀、不可能有正当含义的形状（`ghp_`、`sk-`、`AKIA`、私钥头等），所以报警就是真
的；不做熵值和 `password=` 这类模糊判断，因为在本仓库只产出误报，而一个乱叫的检查会被
`--no-verify` 绕过，比没有检查更糟。它从不打印匹配到的值。

改前端不用重启面板；改 `panel/` 要重启。

`panel/` 必须保持 OS 中立（它要在 Linux 容器里被导入），所有 Windows 专有代码都在仓库根的
`desktop/` 里。测试目录的划分不是装饰：`panel/tests/`（188 个）要能在容器里跑，`tests/`
（13 个）测桌面外壳，那些模块故意不在镜像里。

架构、约定和踩过的坑写在 [`AGENTS.md`](AGENTS.md)，术语表在 [`CONTEXT.md`](CONTEXT.md)，
每个非显然的决定都有一条 [`docs/adr/`](docs/adr/)。

## 它不做什么

- **面板不跑就不签到。** 没有外部调度器，没有服务端组件（ADR-0008），这也是运行方式为什么重要。
- **没有 dry-run。** 界面上点「签到」就是真签到（ADR-0005）。
- **数据不加密。** 三种方式的信任边界都是宿主机本身。
- **没有遥测。** 面板自己只发一个外部请求：推荐卡片清单，不携带任何关于你的信息，
  `PANEL_PROMO=0` 完全关闭（[`docs/promo-cards.md`](docs/promo-cards.md)）。浏览器内核的下载
  由 cloakbrowser 自己发起，见 [`THIRD-PARTY.md`](THIRD-PARTY.md)。

## 许可

本项目 MIT，见 [`LICENSE`](LICENSE)。

第三方组件的许可和义务在 [`THIRD-PARTY.md`](THIRD-PARTY.md)，其中有一条要留意：托盘图标用的
`pystray` 是 **LGPLv3**，且被打进了桌面版 exe。这不要求你的代码闭源（本来就是开源的），但分发
zip 时需要保留那份声明。

`.github/workflows/checkin.yml` 是**废弃代码**，它跑的是上游那个老脚本、不是这个面板，仅作参考
保留（ADR-0008）。fork 之后不要指望它能用。

本项目与它签到的任何站点均无隶属关系。站点的服务条款请自行遵守。
