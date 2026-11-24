"""Passwords, invitation codes and tokens given to a command never reach the log (session._short is what the
command log writes)."""

from openswx import session as S


def test_secret_arguments_are_masked_in_the_log():
    out = S._short({"server": "https://x", "email": "a@b.c", "password": "hunter2", "code": "inv-123",
                    "token": "t0k", "invite": "i", "name": "Part1"})
    assert out == {"server": "https://x", "email": "a@b.c", "password": "***", "code": "***", "token": "***",
                   "invite": "***", "name": "Part1"}
    assert S._short({"password": None}) == {"password": None}  # (nothing given: nothing to hide)
