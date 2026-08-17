from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering


PRERELEASE_KEYWORDS = frozenset(
    {
        "alpha",
        "beta",
        "rc",
        "pre",
        "preview",
        "dev",
        "canary",
        "snapshot",
        "nightly",
    }
)


_VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


@total_ordering
@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> "Version":
        match = _VERSION_RE.fullmatch(value.strip())
        if not match:
            raise ValueError(f"invalid SemVer: {value!r}")
        prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
        for part in prerelease:
            if part.isdigit() and len(part) > 1 and part.startswith("0"):
                raise ValueError(f"invalid numeric prerelease identifier: {value!r}")
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease)

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return base if not self.prerelease else base + "-" + ".".join(self.prerelease)

    def _prerelease_rank(self) -> int:
        if not self.prerelease:
            return 1
        first = self.prerelease[0].lower()
        base = re.sub(r"[\d.]+$", "", first) or first
        if base in PRERELEASE_KEYWORDS:
            return 0
        return 2

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        left = (self.major, self.minor, self.patch)
        right = (other.major, other.minor, other.patch)
        if left != right:
            return left < right
        self_rank = self._prerelease_rank()
        other_rank = other._prerelease_rank()
        if self_rank != other_rank:
            return self_rank < other_rank
        if not self.prerelease:
            return False
        if not other.prerelease:
            return True
        for left_part, right_part in zip(self.prerelease, other.prerelease):
            if left_part == right_part:
                continue
            left_numeric = left_part.isdigit()
            right_numeric = right_part.isdigit()
            if left_numeric and right_numeric:
                return int(left_part) < int(right_part)
            if left_numeric != right_numeric:
                return left_numeric
            left_match = re.match(r"^([A-Za-z]+)(\d+)$", left_part)
            right_match = re.match(r"^([A-Za-z]+)(\d+)$", right_part)
            if left_match and right_match and left_match.group(1) == right_match.group(1):
                return int(left_match.group(2)) < int(right_match.group(2))
            return left_part < right_part
        return len(self.prerelease) < len(other.prerelease)


def _upper_for_caret(version: Version) -> Version:
    if version.major > 0:
        return Version(version.major + 1, 0, 0)
    if version.minor > 0:
        return Version(0, version.minor + 1, 0)
    return Version(0, 0, version.patch + 1)


def _upper_for_tilde(version: Version) -> Version:
    return Version(version.major, version.minor + 1, 0)


def _match_token(version: Version, token: str) -> bool:
    if token in {"", "*", "x", "X"}:
        return True

    wildcard = re.fullmatch(r"(\d+)(?:\.(\d+|x|X|\*))?(?:\.(\d+|x|X|\*))?", token)
    if wildcard and any(value in {"x", "X", "*"} for value in wildcard.groups() if value):
        major, minor, patch = wildcard.groups()
        if version.major != int(major):
            return False
        if minor in {None, "x", "X", "*"}:
            return True
        if version.minor != int(minor):
            return False
        return patch in {None, "x", "X", "*"} or version.patch == int(patch)

    match = re.fullmatch(r"(>=|<=|>|<|=|\^|~)?(.+)", token)
    if not match:
        raise ValueError(f"invalid version range token: {token!r}")
    operator = match.group(1) or "="
    target = Version.parse(match.group(2))
    if operator == ">=":
        return version >= target
    if operator == "<=":
        return version <= target
    if operator == ">":
        return version > target
    if operator == "<":
        return version < target
    if operator == "^":
        return target <= version < _upper_for_caret(target)
    if operator == "~":
        return target <= version < _upper_for_tilde(target)
    return version == target


def satisfies(version: Version | str, range_spec: str) -> bool:
    candidate = Version.parse(version) if isinstance(version, str) else version
    expression = (range_spec or "*").strip()
    if not expression or expression == "*":
        return True

    for branch in expression.split("||"):
        branch = branch.strip().replace(",", " ")
        hyphen = re.fullmatch(r"([^\s]+)\s+-\s+([^\s]+)", branch)
        if hyphen:
            lower = Version.parse(hyphen.group(1))
            upper = Version.parse(hyphen.group(2))
            if lower <= candidate <= upper:
                return True
            continue
        tokens = [token for token in branch.split() if token]
        if all(_match_token(candidate, token) for token in tokens):
            return True
    return False


def validate_range(range_spec: str) -> None:
    expression = (range_spec or "*").strip()
    if not expression:
        raise ValueError("version range must not be empty")
    for branch in expression.split("||"):
        branch = branch.strip().replace(",", " ")
        if not branch:
            raise ValueError(f"invalid empty branch in version range: {range_spec!r}")
        hyphen = re.fullmatch(r"([^\s]+)\s+-\s+([^\s]+)", branch)
        if hyphen:
            Version.parse(hyphen.group(1))
            Version.parse(hyphen.group(2))
            continue
        for token in branch.split():
            _match_token(Version(0, 0, 0), token)
