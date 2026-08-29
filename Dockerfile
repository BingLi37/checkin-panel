# The container way to run the panel. One of three (ADR-0016).
#
#   docker compose up -d
#
# Two stages: Node builds the SPA, Python runs it. Node is not in the final image.

# ---------------------------------------------------------------------------
# Stage 1 — the SPA
# ---------------------------------------------------------------------------
FROM node:24-alpine AS spa

WORKDIR /build

# Lockfile first, so a source-only change does not reinstall node_modules.
COPY frontend/package.json frontend/package-lock.json ./
# --legacy-peer-deps is not a container workaround: plain `npm ci` fails on the host too
# (measured). `@heroui/theme@2.4.26` declares `peerDependencies: tailwindcss >=4.0.0` while
# the project is on `tailwindcss@^3.4.17`, so npm 7+ refuses the tree that `node_modules/` was
# actually installed with. This flag reproduces that same tree rather than papering over a
# difference. The real fix is a frontend decision -- tailwind v4, or a @heroui/theme that
# accepts v3 -- and does not belong to packaging.
RUN npm ci --legacy-peer-deps

COPY frontend/ ./
# `npm run build` is plain `vite build` -- no tsc, so a type error does not fail the image.
# Type-checking stays a separate `npx tsc --noEmit`, as it is for a local build.
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2 — the panel
# ---------------------------------------------------------------------------
FROM python:3.14-slim

# tzdata: `checkin_after` and the scheduler's day boundary are local time
# (service.window_start), so a container on UTC would open a site's window at the wrong hour.
# Set TZ in compose to whatever the accounts' sites use.
#
# The Chromium libraries come from playwright's own `install-deps` rather than a list written
# here: it is maintained against each Debian release, and this image is trixie. The browser
# *binary* is not installed -- see CLOAKBROWSER_CACHE_DIR below.
#
# No CJK fonts on purpose: the panel's UI renders in the reader's own browser, and the browser
# login matches DOM text, not pixels (panel/browser_login.py saves no screenshots). Add
# fonts-noto-cjk only if you start capturing them, and expect ~100MB.
RUN apt-get update \
	&& apt-get install -y --no-install-recommends tzdata \
	&& pip install --no-cache-dir playwright==1.62.0 \
	&& playwright install-deps chromium \
	&& pip uninstall -y -q playwright \
	&& apt-get clean \
	&& rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Into a directory of the same name, not flattened with `./`: browser.txt includes base.txt
# with a bare `-r base.txt`, which pip resolves against the *including file's* directory.
COPY requirements/base.txt requirements/browser.txt ./requirements/
RUN pip install --no-cache-dir -r requirements/browser.txt

# Only what the panel imports. `panel/vendor/` rides along inside this one copy: the
# cloakbrowser helpers and, beside them, the BSD-2 notice clause 2 asks a binary
# redistribution to reproduce — a published image being one.
COPY panel/ ./panel/
COPY run.py pytest.ini ./
COPY --from=spa /build/dist ./frontend/dist

# ADR-0006 says dependencies live inside the project folder. In a container "the project
# folder" is /app, and these three are volumes rather than layers: the database because it is
# the only copy of the accounts (ADR-0003), the profiles because they hold live sessions, and
# the browser because it is ~500MB that must survive `docker compose down`.
ENV CLOAKBROWSER_CACHE_DIR=/app/.local/cloakbrowser \
	CHECKIN_BROWSER_PROFILE_DIR=/app/.browser_profiles \
	PYTHONUNBUFFERED=1

# 0.0.0.0 is not the trust boundary here -- it cannot be. A container reaches its own
# published port through the bridge, so binding loopback inside would make the panel
# unreachable. What limits exposure is compose's `127.0.0.1:8000:8000`, and that is the line
# to change (see docker-compose.yml, and never publish this on a LAN: no auth, and
# /api/accounts answers with credentials in the clear).
ENV PANEL_HOST=0.0.0.0 \
	PANEL_PORT=8000

# Not root: the process that holds plaintext credentials should not own the filesystem it
# runs on. The three directories are created and handed over here so named volumes inherit
# the ownership; a *bind* mount keeps the host's, so chown it to 10001 or the panel cannot
# write its database.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin panel \
	&& mkdir -p /app/data /app/.browser_profiles /app/.local/cloakbrowser \
	&& chown -R panel:panel /app
USER panel

EXPOSE 8000

# /api/health is static and touches no account, so a probe every 30s costs nothing and cannot
# trip a site's rate limit. urllib rather than curl: it is already here.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
	CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"]

CMD ["python", "run.py"]
