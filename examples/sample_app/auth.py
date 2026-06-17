"""Authentication: login, password checking and session tokens."""

import hashlib

from config import SECRET_KEY, SESSION_TTL
from models import User


def hash_password(password):
    return hashlib.sha256((SECRET_KEY + password).encode()).hexdigest()


def authenticate(username, password):
    user = User.find(username)
    if user is None:
        return None
    if user.password_hash == hash_password(password):
        return make_token(user)
    return None


def make_token(user):
    raw = f"{user.username}:{SESSION_TTL}:{SECRET_KEY}"
    return hashlib.sha256(raw.encode()).hexdigest()
