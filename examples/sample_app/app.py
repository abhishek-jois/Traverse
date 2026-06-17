"""Application entry point."""

from config import DEBUG
from routes import login_route, profile_route, users_route

ROUTES = {
    "/login": login_route,
    "/profile": profile_route,
    "/users": users_route,
}


def handle(path, request):
    handler = ROUTES.get(path)
    if handler is None:
        return {"error": "404"}
    return handler(request)


if __name__ == "__main__":
    print("running (debug=%s)" % DEBUG)
