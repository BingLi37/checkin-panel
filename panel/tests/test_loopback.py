"""The socketpair that survives an intercepted loopback (panel/loopback.py).

These tests do not need the machine to be broken: they check the token handshake
directly, and that `install()` leaves a healthy stdlib alone.
"""
import socket

import pytest

from panel import loopback


def test_a_pair_authenticates_itself_and_carries_data():
	ssock, csock = loopback.socketpair()
	try:
		assert ssock.gettimeout() is None  # asyncio sets its own; ours must not linger
		assert csock.gettimeout() is None
		csock.sendall(b'hello')
		assert ssock.recv(16) == b'hello'
		ssock.sendall(b'back')
		assert csock.recv(16) == b'back'
	finally:
		ssock.close()
		csock.close()


def test_two_unrelated_sockets_fail_the_handshake():
	"""The whole point: a stranger's connection is rejected even though it is a
	live, connected socket — which is what an address comparison cannot express."""
	a, b = loopback.socketpair()
	c, d = loopback.socketpair()
	try:
		assert loopback._reaches(a, b)
		assert not loopback._reaches(a, d, timeout=0.3)  # a talks to b, never to d
	finally:
		for s in (a, b, c, d):
			s.close()


def test_a_closed_peer_is_not_a_pair():
	a, b = loopback.socketpair()
	b.close()
	try:
		assert not loopback._reaches(a, b, timeout=0.3)
	finally:
		a.close()


@pytest.mark.parametrize(
	'kwargs',
	[
		{'family': socket.AF_UNIX if hasattr(socket, 'AF_UNIX') else socket.AF_APPLETALK},
		{'type': socket.SOCK_DGRAM},
		{'proto': 6},
	],
)
def test_it_refuses_what_the_stdlib_refuses(kwargs):
	with pytest.raises(ValueError):
		loopback.socketpair(**kwargs)


def test_install_leaves_a_working_stdlib_alone(monkeypatch):
	sentinel = object()
	calls = []

	def healthy():
		calls.append(1)
		return _FakeSocket(), _FakeSocket()

	monkeypatch.setattr(loopback, '_stdlib_socketpair', healthy)
	monkeypatch.setattr(socket, 'socketpair', sentinel)
	assert loopback.install() is False
	assert socket.socketpair is sentinel  # untouched
	assert calls == [1]  # and it actually tried


def test_install_patches_a_broken_stdlib(monkeypatch):
	def broken():
		raise ConnectionError('Unexpected peer connection')

	monkeypatch.setattr(loopback, '_stdlib_socketpair', broken)
	monkeypatch.setattr(socket, 'socketpair', object())
	assert loopback.install() is True
	assert socket.socketpair is loopback.socketpair


def test_install_is_idempotent(monkeypatch):
	def explode():
		raise AssertionError('already patched: the stdlib must not be probed again')

	monkeypatch.setattr(loopback, '_stdlib_socketpair', explode)
	monkeypatch.setattr(socket, 'socketpair', loopback.socketpair)
	assert loopback.install() is True


class _FakeSocket:
	def close(self):
		pass
