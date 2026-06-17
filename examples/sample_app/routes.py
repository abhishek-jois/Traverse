"""HTTP route definitions wiring requests to auth and views."""

from auth import authenticate
from views import list_users, user_profile


def login_route(request):
    token = authenticate(request["username"], request["password"])
    return {"token": token} if token else {"error": "invalid credentials"}


def profile_route(request):
    return user_profile(request["username"])


def users_route(request):
    return {"users": list_users()}
