import base64
import json
import socket
import subprocess
import threading
import time
import urllib.request

import pytest
from fakes import API, contents, responses

from pr_policy_gate.github import GitHubError, network_transport


def unpadded(part):
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def answer(server, status):
    connection, _ = server.accept()
    with connection:
        connection.recv(65536)
        connection.sendall(
            f"HTTP/1.1 {status} Status\r\nX-Test: yes\r\nContent-Length: 4\r\nConnection: close\r\n\r\nbody".encode()
        )


@pytest.mark.parametrize("encoded", [False, True])
def test_installation_lookups_carry_a_jwt_signed_with_the_app_key(key_pair, client, tmp_path, encoded):
    private, public = key_pair
    github, transport = client({}, base64.b64encode(private.encode()).decode() if encoded else private)
    github.token("owner")
    jwt = transport.requests[0].get_header("Authorization").removeprefix("Bearer ")

    header, claims, signature = jwt.split(".")
    (tmp_path / "signature").write_bytes(unpadded(signature))
    verified = subprocess.run(
        ["openssl", "dgst", "-sha256", "-verify", public, "-signature", tmp_path / "signature"],
        input=f"{header}.{claims}".encode(),
        capture_output=True,
        check=False,
    )
    payload = json.loads(unpadded(claims))
    assert verified.stdout == b"Verified OK\n"
    assert payload["iss"] == "Iv23test"
    assert payload["iat"] <= time.time() - 59
    assert payload["exp"] - payload["iat"] == 600


def test_tokens_are_minted_once_per_organization_and_masked(client, capsys):
    github, transport = client(
        responses({"/repos/owner/repo": {"name": "repo"}, "/orgs/other/teams/t/members": [{"login": "a"}]})
    )
    assert github.get("/repos/owner/repo") == {"name": "repo"}
    assert github.get("/repos/owner/repo") == {"name": "repo"}
    assert github.get_all("/orgs/other/teams/t/members", org="other") == [{"login": "a"}]
    requests = [(request.get_method(), request.full_url.removeprefix(API)) for request in transport.requests]
    authorizations = [request.get_header("Authorization") for request in transport.requests]

    assert requests == [
        ("GET", "/repos/owner/repo/installation"),
        ("POST", "/app/installations/1/access_tokens"),
        ("GET", "/repos/owner/repo"),
        ("GET", "/repos/owner/repo"),
        ("GET", "/orgs/other/installation"),
        ("POST", "/app/installations/2/access_tokens"),
        ("GET", "/orgs/other/teams/t/members"),
    ]
    assert authorizations[2:4] == ["Bearer t-owner"] * 2
    assert authorizations[6] == "Bearer t-other"
    assert capsys.readouterr().out == "::add-mask::t-owner\n::add-mask::t-other\n"


def test_get_all_follows_the_next_links(client):
    first, second = "/repos/owner/repo/commits/s/check-runs", "/repositories/9/commits/s/check-runs?page=2"
    github, _ = client(
        {
            ("GET", first): (
                200,
                {"check_runs": [1, 2]},
                {"Link": f'<{API}{second}>; rel="next", <{API}{second}>; rel="last"'},
            ),
            ("GET", second): (200, {"check_runs": [3]}, {"Link": f'<{API}{first}>; rel="prev"'}),
        }
    )

    assert github.get_all(first, key="check_runs") == [1, 2, 3]


def test_an_error_response_names_the_request_and_github_message(client):
    github, _ = client({("PATCH", "/repos/owner/repo/x"): (404, {"message": "Not Found"}, {})})

    with pytest.raises(GitHubError) as raised:
        github.send("PATCH", "/repos/owner/repo/x", {"a": 1})

    assert raised.value.status == 404
    assert str(raised.value) == "PATCH /repos/owner/repo/x failed with HTTP 404 Not Found"


def test_the_key_file_is_private(client, tmp_path):
    client({})

    (key_file,) = tmp_path.glob("*.pem")
    assert key_file.stat().st_mode & 0o777 == 0o600


def test_content_decodes_a_file_and_reports_a_missing_one_as_none(client):
    contents_path = "/repos/owner/repo/contents"
    github, _ = client(
        {
            ("GET", f"{contents_path}/a.yml?ref=s"): contents("rules: []\n"),
            ("GET", f"{contents_path}/missing.yml?ref=s"): (404, {"message": "Not Found"}, {}),
        }
    )

    assert github.content("a.yml", "s") == "rules: []\n"
    assert github.content("missing.yml", "s") is None


def test_content_reports_every_other_error(client):
    github, _ = client({("GET", "/repos/owner/repo/contents/a.yml?ref=s"): (500, {"message": "Server Error"}, {})})

    with pytest.raises(GitHubError) as raised:
        github.content("a.yml", "s")

    assert raised.value.status == 500


def test_content_encodes_the_reserved_characters_of_the_path(client):
    github, _ = client({("GET", "/repos/owner/repo/contents/policy/gate%23v%3F1.yml?ref=s"): contents("{}")})

    assert github.content("policy/gate#v?1.yml", "s") == "{}"


def test_an_error_response_without_a_github_message_names_only_the_request(client):
    github, _ = client({("GET", "/repos/owner/repo/x"): (502, "Bad Gateway", {})})

    with pytest.raises(GitHubError) as raised:
        github.get("/repos/owner/repo/x")

    assert str(raised.value) == "GET /repos/owner/repo/x failed with HTTP 502"


@pytest.mark.parametrize("status", [200, 404])
def test_the_network_transport_returns_the_status_headers_and_body_of_every_response(monkeypatch, status):
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    with socket.create_server(("127.0.0.1", 0)) as server:
        threading.Thread(target=answer, args=(server, status), daemon=True).start()
        request = urllib.request.Request(f"http://127.0.0.1:{server.getsockname()[1]}/")

        code, headers, body = network_transport(request)

    assert (code, headers["X-Test"], body) == (status, "yes", b"body")


def test_app_login_is_the_bot_login_of_the_app_slug(client):
    github, transport = client(responses({"/app": {"slug": "gate-app"}}))

    assert github.app_login == "gate-app[bot]"
    assert github.app_login == "gate-app[bot]"
    assert len(transport.requests) == 1
