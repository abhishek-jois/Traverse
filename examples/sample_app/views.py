"""View functions that render responses for the routes."""

from db import db
from models import User
from utils import truncate


def user_profile(username):
    user = User.find(username)
    if not user:
        return {"error": "not found"}
    return {"username": user.username, "bio": truncate("")}


def list_users():
    return [u.username for u in db.query("users")]
