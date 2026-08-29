"""Make asyncio able to start on a machine whose loopback is intercepted.

`socket.socketpair()` has no Windows syscall behind it, so CPython fakes one: bind a
listener on 127.0.0.1, connect to it, accept, and then check that the two sockets agree
about each other's addresses. Recent CPython added that check to make sure it did not
hand back a stranger's connection.

A local transparent proxy breaks the check without breaking the socket. Measured on this
machine: our client goes out from port 62364, the listener accepts a connection whose
source reads 62367, and `PING`/`PONG` still cross it in both directions — something on
the box relays loopback and rewrites the port on the way. CPython sees the mismatch and
raises `ConnectionError: Unexpected peer connection`.

Nothing survives that. asyncio builds a self-pipe out of a socketpair for *every* event
loop, so `uvicorn.run`, a bare `asyncio.run`, and all 74 async tests die before running
a line of panel code.

So authenticate the pair the way that actually proves it is ours: send a random token
through and see whether it comes out the other end. That is stronger than comparing
addresses (an eavesdropper cannot guess the token) and it is indifferent to a relay
rewriting them. A connection that fails the handshake is somebody else's; close it and
accept the next one, which is what CPython would have wanted to do.

`install()` patches nothing unless the stdlib version is actually broken here, so a
healthy machine keeps stock behaviour and this file costs one socketpair at startup.
"""
import secrets
import socket

TOKEN_LEN = 16
HANDSHAKE_TIMEOUT_S = 5.0
ACCEPT_ATTEMPTS = 4  # each rejected connection is one stranger; ours is still queued

_stdlib_socketpair = socket.socketpair


def _reaches(sender: socket.socket, receiver: socket.socket, timeout: float = HANDSHAKE_TIMEOUT_S) -> bool:
	"""Does a random token written on `sender` come out of `receiver`?

	The proof of a pair. False for a stranger's connection (nothing arrives, so this
	blocks until the timeout) and False for a half-open one.
	"""
	token = secrets.token_bytes(TOKEN_LEN)
	try:
		# Inside the try: a socket that died between accept() and here fails even at
		# settimeout, and that is a connection to reject, not an error to raise.
		sender.settimeout(timeout)
		receiver.settimeout(timeout)
		sender.sendall(token)
		received = b''
		while len(received) < TOKEN_LEN:
			chunk = receiver.recv(TOKEN_LEN - len(received))
			if not chunk:  # peer hung up mid-handshake
				return False
			received += chunk
	except OSError:
		return False
	return received == token


def socketpair(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
	"""`socket.socketpair` that authenticates with a token instead of with addresses.

	Same signature and same guarantees as the stdlib one; the pair comes back blocking
	and timeout-free, because asyncio sets its own.
	"""
	if family not in (socket.AF_INET, socket.AF_INET6):
		raise ValueError('Only AF_INET and AF_INET6 socket address families are supported')
	if type != socket.SOCK_STREAM:
		raise ValueError('Only SOCK_STREAM socket type is supported')
	if proto != 0:
		raise ValueError('Only protocol zero is supported')

	host = '127.0.0.1' if family == socket.AF_INET else '::1'
	lsock = socket.socket(family, type, proto)
	try:
		lsock.bind((host, 0))
		lsock.listen()
		addr, port = lsock.getsockname()[:2]
		csock = socket.socket(family, type, proto)
		try:
			csock.setblocking(False)  # connect and accept from one thread, as the stdlib does
			try:
				csock.connect((addr, port))
			except (BlockingIOError, InterruptedError):
				pass
			csock.setblocking(True)
			lsock.settimeout(HANDSHAKE_TIMEOUT_S)
			for _ in range(ACCEPT_ATTEMPTS):
				ssock, _peer = lsock.accept()
				# Both directions: asyncio writes on csock and reads on ssock, and a relay
				# that only forwards one way would pass a one-way check and then deadlock.
				if _reaches(ssock, csock) and _reaches(csock, ssock):
					ssock.settimeout(None)
					csock.settimeout(None)
					return ssock, csock
				ssock.close()
			raise ConnectionError('socketpair: no connection authenticated as our own')
		except BaseException:
			csock.close()
			raise
	finally:
		lsock.close()


def install() -> bool:
	"""Swap in the token-authenticated socketpair, but only if the stdlib one is broken.

	Returns True when the patch was needed. Call before anything creates an event loop.
	"""
	if socket.socketpair is socketpair:
		return True
	try:
		ssock, csock = _stdlib_socketpair()
	except ConnectionError:
		socket.socketpair = socketpair
		return True
	ssock.close()
	csock.close()
	return False
