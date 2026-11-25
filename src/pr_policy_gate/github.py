import base64
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from functools import cached_property
from pathlib import Path
from typing import Any
from urllib.parse import quote

type Response = tuple[int, dict[str, str], bytes]
type Transport = Callable[[urllib.request.Request], Response]


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def network_transport(request: urllib.request.Request) -> Response:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def bot_login(app_slug: str) -> str:
    return f"{app_slug}[bot]"


class GitHub:
    def __init__(
        self,
        api_url: str,
        repository: str,
        owner: str,
        client_id: str,
        private_key: str,
        scratch: Path,
        transport: Transport,
    ):
        self.repository = repository
        self.owner = owner
        self._api_url = api_url
        self._client_id = client_id
        self._transport = transport
        self._tokens: dict[str, str] = {}
        self._key_path = scratch / "app-private-key.pem"
        self._key_path.touch(mode=0o600)
        self._key_path.write_text(
            private_key if private_key.startswith("-----BEGIN") else base64.b64decode(private_key).decode()
        )

    @cached_property
    def app_login(self) -> str:
        return bot_login(self._request("GET", "/app", self._jwt())[0]["slug"])

    def token(self, org: str) -> str:
        if org not in self._tokens:
            path = f"/repos/{self.repository}/installation" if org == self.owner else f"/orgs/{org}/installation"
            installation, _ = self._request("GET", path, self._jwt())
            access, _ = self._request("POST", f"/app/installations/{installation['id']}/access_tokens", self._jwt())
            print(f"::add-mask::{access['token']}", flush=True)
            self._tokens[org] = access["token"]
        return self._tokens[org]

    def get(self, path: str, org: str | None = None) -> Any:
        return self._request("GET", path, self.token(org or self.owner))[0]

    def get_all(self, path: str, key: str | None = None, org: str | None = None) -> list[Any]:
        items: list[Any] = []
        url: str | None = path
        while url:
            page, headers = self._request("GET", url, self.token(org or self.owner))
            items += page[key] if key else page
            url = _next_url(headers)
        return items

    def send(self, method: str, path: str, body: dict | None = None) -> Any:
        return self._request(method, path, self.token(self.owner), body)[0]

    def content(self, path: str, ref: str) -> str | None:
        try:
            file = self.get(f"/repos/{self.repository}/contents/{quote(path)}?ref={ref}")
        except GitHubError as error:
            if error.status == 404:
                return None
            raise
        return base64.b64decode(file["content"]).decode()

    def _jwt(self) -> str:
        now = int(time.time())
        claims = {"iat": now - 60, "exp": now + 540, "iss": self._client_id}
        signing_input = f"{_encode({'alg': 'RS256', 'typ': 'JWT'})}.{_encode(claims)}"
        signature = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", self._key_path],
            input=signing_input.encode(),
            capture_output=True,
            check=True,
        ).stdout
        return f"{signing_input}.{_base64url(signature)}"

    def _request(self, method: str, url: str, token: str, body: dict | None = None) -> tuple[Any, dict[str, str]]:
        request = urllib.request.Request(
            url if url.startswith("https://") else f"{self._api_url}{url}",
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "pr-policy-gate",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        status, headers, payload = self._transport(request)
        if status >= 400:
            path = url.removeprefix(self._api_url)
            raise GitHubError(status, f"{method} {path} failed with HTTP {status} {_message(payload)}".strip())
        return (json.loads(payload) if payload else None), headers


def _message(payload: bytes) -> str:
    try:
        return json.loads(payload).get("message", "")
    except (ValueError, AttributeError):
        return ""


def _next_url(headers: dict[str, str]) -> str | None:
    link = next((value for name, value in headers.items() if name.lower() == "link"), "")
    match = re.search(r'<([^>]+)>;\s*rel="next"', link)
    return match[1] if match else None


def _encode(value: dict) -> str:
    return _base64url(json.dumps(value, separators=(",", ":")).encode())


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
