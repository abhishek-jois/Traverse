"""Tests for the authentication module."""

from auth import authenticate, hash_password
from models import User


def test_authenticate_success():
    User("alice", hash_password("secret")).save()
    assert authenticate("alice", "secret") is not None


def test_authenticate_failure():
    assert authenticate("ghost", "nope") is None
