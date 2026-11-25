import base64
import json
import subprocess

API = "https://api.example"
INSTALLATIONS = {
    ("GET", "/repos/owner/repo/installation"): (200, {"id": 1}, {}),
    ("POST", "/app/installations/1/access_tokens"): (201, {"token": "t-owner"}, {}),
    ("GET", "/orgs/other/installation"): (200, {"id": 2}, {}),
    ("POST", "/app/installations/2/access_tokens"): (201, {"token": "t-other"}, {}),
}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def init_remote(repo):
    git(repo, "init", "--quiet", "--initial-branch=main")
    for key, value in [
        ("user.email", "test@example.com"),
        ("user.name", "Test"),
        ("uploadpack.allowFilter", "true"),
        ("uploadpack.allowAnySHA1InWant", "true"),
    ]:
        git(repo, "config", key, value)


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def responses(bodies):
    return {("GET", path): (200, body, {}) for path, body in bodies.items()}


def contents(text):
    return 200, {"content": base64.b64encode(text.encode()).decode()}, {}


class FakeTransport:
    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        status, body, headers = self.routes[(request.get_method(), request.full_url.removeprefix(API))]
        return status, headers, json.dumps(body).encode()

    def sent(self, method):
        return [
            (request.full_url.removeprefix(API), json.loads(request.data) if request.data else None)
            for request in self.requests
            if request.get_method() == method
        ]
