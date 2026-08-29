# A machine that relays its own loopback

The panel stopped starting. Not a check-in failure and nothing to do with any site — `uvicorn.run` died in `asyncio`, before a line of panel code ran:

```
ConnectionError: Unexpected peer connection
  socket.py, _fallback_socketpair
  proactor_events.py, _make_self_pipe
```

`socket.socketpair()` has no Windows syscall behind it, so CPython fakes one: bind a listener on `127.0.0.1`, connect to it, accept, and then check the two sockets agree about each other's addresses — a guard against handing back a stranger's connection. asyncio builds a self-pipe from a socketpair for **every** event loop, so a machine where that check fails cannot run *any* async Python. Measured: `uvicorn.run`, a bare `asyncio.run`, and all 74 async tests, all dead at the same line.

The connection is not a stranger's. Measured on this machine: our client goes out from port 62364, the listener accepts a connection whose source reads 62367, and `PING`/`PONG` still cross it in **both** directions. Something local relays loopback and rewrites the port on the way — the port the project already assumes is a proxy (`CHECKIN_PROXY_URL` defaults to `127.0.0.1:7897`) is the same class of software. The socket is perfectly good; only the address comparison is wrong about it.

So `panel/loopback.py` authenticates the pair the way that actually proves it is ours: **send a random token through and see whether it comes out the other end**, in both directions, and accept the next connection if it does not. That is strictly stronger than comparing addresses — an eavesdropper cannot guess 16 random bytes, whereas anything that can connect to the port can produce a matching address — and it is indifferent to a relay rewriting them. Both directions, because asyncio writes on one end and reads on the other, and a one-way relay would pass a single check and then deadlock.

`install()` probes the stdlib first and patches nothing when it works, so this is a no-op on a healthy machine and costs one socketpair at startup. It is called from `run.py` (before `uvicorn` is imported) and from `panel/tests/conftest.py` (before any test builds a loop) — the two places that own an entry point.

**Consequences**:
- The panel and the whole test suite run on this machine again: 74 errors → 103 passing.
- The fix lives in the repo rather than in the machine's proxy settings, so it survives a reinstall and does not ask the owner to turn off the proxy the browser login needs.
- Rejected: pinning an older Python. The address check is in current CPython and going backwards to dodge it trades a working guard for an old one.
- Rejected: patching `socket.socketpair` unconditionally. A healthy machine should keep stock behaviour; a fix that only engages when the stdlib actually fails cannot regress one.
- A rejected connection gets 16 random bytes written to it before being closed. Harmless, and the price of finding ours.
- `run.py` prints one line when the patch engages, so "why is this machine special" is answerable from the log a year from now.
