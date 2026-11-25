import re
from collections.abc import Iterable, Sequence
from functools import cache

PLACEHOLDER = "{*}"


def matches(patterns: Sequence[str], path: str, value: str | None = None) -> bool:
    matched = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        if _compile(pattern[1:] if negated else pattern, value).fullmatch(path):
            matched = not negated
    return matched


def captured_values(patterns: Sequence[str], paths: Iterable[str]) -> list[str]:
    capturing = [
        _compile(pattern, None) for pattern in patterns if not pattern.startswith("!") and PLACEHOLDER in pattern
    ]
    return sorted({match["value"] for path in paths for regex in capturing if (match := regex.fullmatch(path))})


@cache
def _compile(pattern: str, value: str | None) -> re.Pattern[str]:
    tokens: list[str] = []
    quantifiable = False
    i = 0
    while i < len(pattern):
        if pattern.startswith(PLACEHOLDER, i):
            token, quantifiable, i = "(?P<value>[^/]+)" if value is None else re.escape(value), False, i + 3
        elif pattern.startswith("**/", i):
            token, quantifiable, i = "(?:.*/)?", False, i + 3
        elif pattern.startswith("**", i):
            token, quantifiable, i = ".*", False, i + 2
        elif pattern[i] == "*":
            token, quantifiable, i = "[^/]*", False, i + 1
        elif pattern[i] in "?+" and quantifiable:
            token, quantifiable, i = pattern[i], False, i + 1
        elif pattern[i] == "[" and (end := pattern.find("]", i + 2)) != -1:
            token, quantifiable, i = f"[{_character_class(pattern[i + 1 : end])}]", True, end + 1
        else:
            token, quantifiable, i = re.escape(pattern[i]), True, i + 1
        tokens.append(token)
    return re.compile("".join(tokens))


def _character_class(members: str) -> str:
    return "".join(member if member.isalnum() or member == "-" else re.escape(member) for member in members)
