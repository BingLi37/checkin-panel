# 自动签到面板

给 New API 类型的中转站做每日签到的自建面板。加账号、看余额、每天自动领，一台机器上跑，不依赖
任何外部服务。

优先走 HTTP 协议签到，只有站点确实拦得住协议（OAuth 会话过期、Turnstile、WAF）才启动浏览器 ——
所以绝大多数账号的日常签到只是几个 HTTP 请求，快且不吃资源。

已实测适配 anyrouter.top、agentrouter.org、seekai.cc、sotamodel.net，每家的坑都不一样：有的
把签到路由藏在自己的名字下、有的用 JWT 加轮换 cookie、有的整个 API 挡在 WAF 后面。代码里每
个看起来奇怪的判断都是为其中一条踩出来的，注释就写在那一行旁边。

![面板主界面：账号列表，每行一个账号，显示站点、登录方式、今天签到成功没有、余额和最近一次运行时间](docs/images/panel.png)

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

装好之后加第一个账号：右上角**添加账号**，填站点地址点**检测** —— 签到方式和这个站点认哪几种
登录方式都由探测结果决定，不用你自己判断。

![添加账号弹窗：名称、网站地址、签到方式、登录方式、用户名和密码](docs/images/add-account.png)

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
| OAuth-only，但签到是 POST 一个路由（`endpoint`） | **不需要浏览器。** 站点有访问令牌就粘令牌，没有就粘它的 session，见方案 A |
| OAuth-only，且靠重新登录或加载页面发额度（`login_bonus` / `visit`） | 需要，但只在 IdP 会话失效时，通常数周到数月一次 |
| 站点 API 被 WAF 挡住（anyrouter.top） | 需要，且**每天**都要 —— 只有真浏览器能过那道 JS 挑战，任何粘进来的凭据都没用 |

访问令牌是**账号自己的**属性，不是"登录方式"的属性：哪怕这个站只让你用 GitHub / LinuxDO 登，
登进去之后照样能在用户页生成一个令牌。所以第二行那种账号，多数也能靠令牌免掉浏览器 —— 值得先去
站点用户页看一眼有没有这个入口。

**所以第一条建议是：能绑密码的就绑密码。** 这不是绕过问题，是让问题不存在 —— 密码账号的日常
签到完全不碰浏览器。添加账号时如果站点允许设密码，面板会提示「能设密码就转成纯 HTTP」。

绑不了密码的站点走下面：方案 A 最省事，够不着的再看 B。anyrouter.top（WAF + `visit` 机制，
每次都要浏览器）只能走 B 往后。

### 方案 A：把站点自己的凭据粘过去（最直接）

服务器上不用开窗口、不用装 X，面板也不用开浏览器。搬的是**站点发给你的凭据**，上游那家（GitHub /
LinuxDO / 谷歌）一点都不用碰 —— 站点当初用什么方式登的，不影响这条路。

站点给的凭据有两种，能用第一种就别用第二种。

**首选：访问令牌。** 多数 New API 站点的用户页里有「访问令牌 / Access Token」（本仓库实测
`api.hcnsec.cn`：`/profile` → 访问令牌 → 生成）。它是明文一串，站点界面上直接复制，**不需要任何
浏览器扩展**，也不像 cookie 那样一个月就过期。面板里**登录方式**选「Access Token」粘进去即可 ——
面板把它作为 `Authorization` 头发出去，站点的 `/api/user/self` 和签到路由都认。

**退路：会话 Cookie。** 站点不提供访问令牌时才用这条。

1. 在自己电脑的浏览器里打开**这个签到站点**并登录。
2. 取出 `session` 这条 cookie 的**值**。它通常是 **HttpOnly** 的（实测 `api.hcnsec.cn`：
   `HttpOnly; SameSite=Strict; Max-Age=2592000`），所以页面 JS 读不到；用浏览器自带的
   **DevTools → Application → Cookies → 该站点 → `session` → 复制 Value** 最稳。
   cookie 扩展也能读 HttpOnly，但得先确认它在这个站点上有权限、且停在**站点自己的页面**上 ——
   停在面板上（`127.0.0.1:8000`）会显示 "This page does not have any cookies"，因为面板自己不发
   cookie。
3. 面板里 → **登录方式**选「会话 Cookie」→ 粘进会话栏 → 保存。

会话栏既收单独一个值，也收 cookie 扩展导出的整段 JSON；整段粘就行，面板自己挑出要用的那条：普通
站点是 `session`，JWT 站点（如 seekai.cc）是 `new_api_refresh`。挑哪条由**探测结果**决定，不是由
粘进来的内容决定。两种粘法保存时都立刻验证一次，成没成当场就知道。

还有一种情况值得先知道：cookie 是好的，但站点还要账号自己的用户 id（`new-api-user` 头），
不给它就每个接口都 401。面板认得出这一种，会直接说「凭据本身没问题，但这个站点还要账号的用户
id」，而不是笼统地报凭据无效。把这个值填进弹窗里的 **API User（可选）**：站点页面 F12 → 网络，
挑一个站点自己发出的 API 请求，请求头里的 `New-Api-User` 就是它（localStorage 里的 `user.id`
是同一个数）。

![DevTools 网络面板：站点自己发的 sign_in 请求，请求头里有 New-Api-User 一行](docs/images/api-user.png)

两个限制，界面上也会直接提示：

- **面板续不了会话 cookie。** 过期就得再粘一次（`api.hcnsec.cn` 实测 30 天）。访问令牌没这个问题，
  有密码的账号面板能自己重新登录，OAuth-only 的走方案 B 一次管数周到数月。
- **只够 `endpoint` 站点用。** `login_bonus` 靠重新登录发额度（协议层面就要密码），`visit` 要
  真的在登录状态下加载页面（anyrouter.top），这两种都不是一个 cookie 或令牌能替代的。

顺一句：这个站点如果还开着密码登录、又不要 Turnstile（`api.hcnsec.cn` 就是这样），那连粘都不用粘 ——
直接填账号密码，面板每天纯 HTTP 跑完，凭据永不过期。先试这个。

### 方案 B：OAuth-only 身份，把 IdP 会话注入进去

站点只有 GitHub / LinuxDO 登录、密码根本设不了（ADR-0009），而方案 A 那条站点会话过期了 ——
这时候搬**上游那一层**，搬完面板自己每天去换站点会话，不用再管。要点是分清两层：

| 哪一层 | 存在哪 | 活多久 | 谁去拿 |
|---|---|---|---|
| 站点会话 | 数据库 `accounts.session` | 短，每天自动换 | 面板自己（无头 OAuth 跳转） |
| **IdP 会话**（linux.do / github.com） | 浏览器 profile 目录 | 数周到数月 | **人，一次** |

1. 在自己电脑的浏览器里登录 LinuxDO 或 GitHub。
2. 用 cookie 扩展，**停在 linux.do / github.com 的页面上**导出。

   ![Cookie-Editor 扩展：右下角导出按钮，格式选 JSON](docs/images/cookie-editor.png)

3. 面板列表里点这个账号的**「注入会话」**，整段粘进去，保存。

   ![注入 GitHub 会话弹窗：把导出的整段 JSON 粘进文本框，下面是「注入后立刻验证一次」开关](docs/images/inject-session.png)

默认勾着「注入后立刻验证一次」：面板当场跑一次无头授权登录，成没成马上告诉你，而不是等到明天
定时签到失败才发现。三种结果分别是「验证通过」「登录没通过 + 原因」「未验证」——只有第一种算证据。

三点要清楚：

- **粘过去的是你整个论坛 / GitHub 账号**，不只是签到站点的凭据。它写进那个账号的浏览器 profile，
  面板不会存进数据库；但面板本身没有登录保护（ADR-0003），谁能访问面板就能用这个身份。
  所以 `PANEL_HOST` / 端口发布范围在这一步之后更要紧，不是更不要紧。
- 会话过期了就再导一次，频率和方案 C / D 一样，都是数周到数月。
- 注入后的**第一次**无头跳转是这条路上唯一没被实测过的环节：注入来的会话和浏览器自己登出来的
  会话，在 Cloudflare 眼里是否等价，仓库里没有证据。所以别关掉那个验证开关；万一不通，方案 C
  和 D 仍然在下面。
- **删账号时会问你要不要一起删掉这个 profile，默认删。** 上面那张表里 IdP 会话就住在这个目录里，
  所以留下它等于把一份还能用的论坛 / GitHub 登录留在硬盘上，而账号已经不在面板里了。选择留下的话，
  它之后会出现在标题旁边的**「清理 profile」**里 —— 改过名字的账号也会在那儿留一份，因为 profile
  是按名字建目录的。

  ![清理浏览器 profile 弹窗：列出没有账号认领的 profile 和它们占的空间](docs/images/profile-cleanup.png)

### 方案 C：容器里临时开一个 VNC，看着窗口点

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

### 方案 D：SSH X11 转发

```bash
ssh -X you@your-server
# 容器里：docker exec -e DISPLAY=$DISPLAY -it checkin-panel ...
```

窗口直接开在你自己屏幕上，服务器上不留任何常驻暴露。代价是本机要有 X 服务器（Windows 上要装
VcXsrv 一类），容器场景还要把 `DISPLAY` 和 X socket 传进去，比方案 C 绕。

### 一条走不通的路：拷 profile 目录

**别想着「在本地授权好再把整个 profile 目录拷到服务器」。** Windows 的 profile 里
`Local State` 有 `os_crypt.encrypted_key`，那是 DPAPI 加密、绑当前 Windows 账户的，搬到 Linux
解不开 cookie。

**但这恰恰是方案 A / B 能成的原因**，两件事别混在一起：

| 搬什么 | 结果 | 为什么 |
|---|---|---|
| 整个 profile 目录 | 不行 | 里面的 cookie 是用 DPAPI 密钥加密的，那把钥匙搬不走 |
| cookie 的**值**（导出的 JSON） | 行 | 明文的名值对，接收方浏览器用它自己的密钥重新加密 |

数据库里的 `session` 字段本来就能搬（纯字符串），那就是方案 A；`visit` 类账号的站点会话在 profile
里，但它每天由无头跳转自己重拿，所以那种账号要搬的是上面那层 IdP 会话。

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
.venv\Scripts\python.exe -m pytest              :: 285 个测试
cd frontend && npm run dev                       :: 前端热重载，:5173 代理到 :8000
```

Fork 之后建议先装上这个钩子，它会在 `git commit` 时拦下形似凭据的字符串和数据库文件：

```bat
.venv\Scripts\python.exe scripts\check_secrets.py --install
.venv\Scripts\python.exe scripts\check_secrets.py --all   :: 或手动全树扫一遍
```

它只匹配有固定前缀、不可能有正当含义的形状（`ghp_`、`sk-`、`AKIA`、私钥头等），所以报警就是真
的；不做熵值和 `password=` 这类模糊判断，因为在本仓库只产出误报，而一个乱叫的检查会被
`--no-verify` 绕过，比没有检查更糟。它从不打印匹配到的值。

改前端不用重启面板；改 `panel/` 要重启。

`panel/` 必须保持 OS 中立（它要在 Linux 容器里被导入），所有 Windows 专有代码都在仓库根的
`desktop/` 里。`panel/tests/` 的 285 个测试因此必须能在容器里跑——桌面外壳的测试不在这里，
它们跟开发树放在一起。

代码里的注释密度不低，这是故意的：每个不显然的判断旁边都写着它为什么长这样，而不是只写它做
了什么。注释里形如 `ADR-0007` 的标记指向开发树里的决策记录，那些文件不在本仓库——把它当成一
个「这里有个非显然的取舍，理由见旁边这段注释」的记号就行，注释本身是自足的。

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

本仓库没有 GitHub Actions 签到工作流。面板自己调度（ADR-0008），fork 之后也不需要配那些 secret。

本项目与它签到的任何站点均无隶属关系。站点的服务条款请自行遵守。
