import json
from pathlib import Path

import pytest

from pr_policy_gate import schema
from pr_policy_gate.config import parse_yaml

FIXTURES = Path(__file__).parent / "fixtures" / "schema"
VALID = {path.stem: path for path in sorted((FIXTURES / "valid").glob("*.yml"))}
INVALID = json.loads((FIXTURES / "invalid.json").read_text())


@pytest.mark.parametrize("name", VALID)
def test_the_validator_accepts_the_valid_cases(name):
    assert schema.validate(parse_yaml(VALID[name].read_text()), schema.SCHEMA) == []


@pytest.mark.parametrize("name", INVALID)
def test_the_validator_rejects_the_invalid_cases(name):
    assert schema.validate(INVALID[name], schema.SCHEMA)


def test_an_unsupported_keyword_fails_loudly():
    with pytest.raises(ValueError, match="format"):
        schema.validate("x", {"format": "email"})
