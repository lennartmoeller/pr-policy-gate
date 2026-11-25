import subprocess

import pytest
from fakes import API, INSTALLATIONS, FakeTransport

from pr_policy_gate.github import GitHub


@pytest.fixture(scope="session")
def key_pair(tmp_path_factory):
    directory = tmp_path_factory.mktemp("keys")
    private, public = directory / "private.pem", directory / "public.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-out", private], check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", private, "-pubout", "-out", public], check=True, capture_output=True)
    return private.read_text(), public


@pytest.fixture
def client(key_pair, tmp_path):
    def connect(routes, private_key=key_pair[0]):
        transport = FakeTransport(INSTALLATIONS | routes)
        return GitHub(API, "owner/repo", "owner", "Iv23test", private_key, tmp_path, transport), transport

    return connect
