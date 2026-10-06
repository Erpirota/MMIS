"""Version representations, parsers, and range matchers (Fabric & Maven).

Pure stdlib, no external dependencies, tri-state matching.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# Regex for standard numeric/semver versions in Fabric
_VERSION_RE = re.compile(
    r"^v?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$"
)

# Operator pattern for Fabric predicates
_FABRIC_OP_RE = re.compile(r"^(\^|~>|~|>=|<=|>|<|=)?\s*(.*)$")


@dataclass(frozen=True)
class ComparableVersion:
    """Parsed version with numerical components, prerelease tokens, and build info for Fabric SemVer."""

    raw: str
    components: tuple[int, ...]
    prerelease: tuple[int | str, ...]
    build: tuple[str, ...]

    @classmethod
    def parse(cls, v_str: str) -> ComparableVersion | None:
        """Parse version string into ComparableVersion, or None if non-comparable."""
        if not v_str:
            return None
        v_str = v_str.strip()
        m = _VERSION_RE.match(v_str)
        if not m:
            return None

        # Parse numeric components
        comp_str = m.group(1)
        try:
            components = tuple(int(part) for part in comp_str.split("."))
        except ValueError:
            return None

        # Parse prerelease
        pre_str = m.group(2)
        if pre_str:
            pre_parts: list[int | str] = []
            for part in pre_str.split("."):
                if part.isdigit():
                    pre_parts.append(int(part))
                else:
                    pre_parts.append(part)
            prerelease = tuple(pre_parts)
        else:
            prerelease = ()

        # Parse build
        build_str = m.group(3)
        if build_str:
            build = tuple(build_str.split("."))
        else:
            build = ()

        return cls(
            raw=v_str,
            components=components,
            prerelease=prerelease,
            build=build,
        )

    def compare(self, other: ComparableVersion) -> int:
        """Compare two Fabric SemVer versions according to SemVer 2.0 rules.

        Returns -1 if self < other, 0 if self == other, 1 if self > other.
        Build metadata is ignored.
        """
        # 1. Compare numeric components padded to equal length
        max_len = max(len(self.components), len(other.components))
        c1 = self.components + (0,) * (max_len - len(self.components))
        c2 = other.components + (0,) * (max_len - len(other.components))

        if c1 < c2:
            return -1
        if c1 > c2:
            return 1

        # 2. Components are equal. Check prerelease
        # A release (no prerelease) has higher precedence than a prerelease
        if not self.prerelease and other.prerelease:
            return 1
        if self.prerelease and not other.prerelease:
            return -1
        if not self.prerelease and not other.prerelease:
            return 0

        # Both have prerelease. Compare identifier by identifier
        for p1, p2 in zip(self.prerelease, other.prerelease):
            if p1 == p2:
                continue
            is_p1_num = isinstance(p1, int)
            is_p2_num = isinstance(p2, int)
            if is_p1_num and not is_p2_num:
                # Numeric identifiers always have lower precedence than alphanumeric
                return -1
            if not is_p1_num and is_p2_num:
                return 1
            if is_p1_num and is_p2_num:
                return -1 if p1 < p2 else 1
            # Both string
            return -1 if str(p1) < str(p2) else 1

        # All shared identifiers equal; larger set has higher precedence
        if len(self.prerelease) < len(other.prerelease):
            return -1
        if len(self.prerelease) > len(other.prerelease):
            return 1

        return 0


# =========================================================================
# Maven ComparableVersion Implementation
# =========================================================================

def _normalize_maven_tokens(tokens: list[int | str]) -> list[int | str]:
    normalized: list[int | str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == 0:
            j = i
            while j < len(tokens) and tokens[j] == 0:
                j += 1
            if j == len(tokens) or isinstance(tokens[j], str):
                if not normalized and j == len(tokens):
                    normalized.append(0)
                i = j
                continue
        normalized.append(tok)
        i += 1
    return normalized


def tokenize_maven_version(v_str: str) -> list[int | str]:
    """Tokenize Maven version string by '.', '-', transition between letters and digits.

    Insensitive to trailing zeros (1.0 == 1.0.0, 1.0-SNAPSHOT == 1-SNAPSHOT).
    """
    if not v_str:
        return []
    s = v_str.strip()
    raw_tokens: list[str] = []
    curr: list[str] = []
    mode: str | None = None

    for ch in s:
        if ch in ".-_":
            if curr:
                raw_tokens.append("".join(curr))
                curr = []
            mode = None
        elif ch.isdigit():
            if mode == "alpha":
                if curr:
                    raw_tokens.append("".join(curr))
                    curr = []
            mode = "digit"
            curr.append(ch)
        elif ch.isalpha():
            if mode == "digit":
                if curr:
                    raw_tokens.append("".join(curr))
                    curr = []
            mode = "alpha"
            curr.append(ch)
        else:
            if curr:
                raw_tokens.append("".join(curr))
                curr = []
            mode = None

    if curr:
        raw_tokens.append("".join(curr))

    typed_tokens: list[int | str] = []
    for tok in raw_tokens:
        if tok.isdigit():
            typed_tokens.append(int(tok))
        else:
            typed_tokens.append(tok.lower())

    return _normalize_maven_tokens(typed_tokens)


def _maven_item_rank(item: int | str) -> tuple[int, Any]:
    """Rank item per Maven ordering:

    alpha(a) < beta(b) < milestone(m) < rc(cr) < snapshot < "" (release) < sp < other strings < numbers
    """
    if isinstance(item, int):
        return (3, item)
    s = str(item).lower()
    if s in ("alpha", "a"):
        return (-6, "")
    if s in ("beta", "b"):
        return (-5, "")
    if s in ("milestone", "m"):
        return (-4, "")
    if s in ("rc", "cr"):
        return (-3, "")
    if s == "snapshot":
        return (-2, "")
    if s in ("", "ga", "final", "release"):
        return (0, "")
    if s == "sp":
        return (1, "")
    return (2, s)


def compare_maven_versions(v1_str: str, v2_str: str) -> int:
    """Compare two version strings under Maven ComparableVersion semantics.

    Returns -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2.
    """
    t1 = tokenize_maven_version(v1_str)
    t2 = tokenize_maven_version(v2_str)
    max_len = max(len(t1), len(t2))
    for i in range(max_len):
        item1 = t1[i] if i < len(t1) else ""
        item2 = t2[i] if i < len(t2) else ""
        r1 = _maven_item_rank(item1)
        r2 = _maven_item_rank(item2)
        if r1 < r2:
            return -1
        if r1 > r2:
            return 1
    return 0


# =========================================================================
# Fabric Predicate Evaluator
# =========================================================================

def _parse_fabric_single_predicate(token: str) -> tuple[str, str] | None:
    """Parse a single Fabric predicate token like '>=1.20.1' into (operator, version_str)."""
    token = token.strip()
    if not token or token == "*":
        return ("*", "")

    m = _FABRIC_OP_RE.match(token)
    if not m:
        return None

    op = m.group(1) or ""
    v_str = m.group(2).strip()

    # Wildcards in version string like 1.20.x, 1.20.*, 1.x
    if v_str.endswith(".x") or v_str.endswith(".X") or v_str.endswith(".*"):
        base = v_str[:-2]
        return ("wildcard", base)
    if v_str in ("x", "X", "*"):
        return ("*", "")

    if not op:
        op = "="
    elif op == "~>":
        op = "~"

    return (op, v_str)


def _eval_fabric_atomic(op: str, v_str: str, target_str: str, target: ComparableVersion | None) -> bool | None:
    """Evaluate a single atomic Fabric condition against target."""
    if op == "*":
        return True

    if target is None:
        return None

    if op == "wildcard":
        base_v = ComparableVersion.parse(v_str)
        if base_v is None:
            return None
        lower_comps = base_v.components
        upper_comps = base_v.components[:-1] + (base_v.components[-1] + 1,)
        lower_v = ComparableVersion(raw="", components=lower_comps, prerelease=(), build=())
        upper_v = ComparableVersion(raw="", components=upper_comps, prerelease=(), build=())
        return target.compare(lower_v) >= 0 and target.compare(upper_v) < 0

    target_ref = ComparableVersion.parse(v_str)
    if target_ref is None:
        return None

    cmp = target.compare(target_ref)

    if op == "=":
        return cmp == 0
    if op == ">":
        return cmp > 0
    if op == ">=":
        return cmp >= 0
    if op == "<":
        return cmp < 0
    if op == "<=":
        return cmp <= 0

    if op == "~":
        comps = list(target_ref.components)
        if len(comps) >= 2:
            upper_comps = tuple(comps[:1] + [comps[1] + 1] + [0] * (len(comps) - 2))
        else:
            upper_comps = tuple([comps[0] + 1] + [0] * (len(comps) - 1))
        upper_v = ComparableVersion(raw="", components=upper_comps, prerelease=(), build=())
        return cmp >= 0 and target.compare(upper_v) < 0

    if op == "^":
        # In fabric-loader, ^ means '>= given AND same major', with no special case for 0.x
        major = target_ref.components[0] if target_ref.components else 0
        upper_comps = (major + 1,)
        upper_v = ComparableVersion(raw="", components=upper_comps, prerelease=(), build=())
        return cmp >= 0 and target.compare(upper_v) < 0

    return None


def _eval_fabric_and_clause(clause: str, target_str: str, target: ComparableVersion | None) -> bool | None:
    """Evaluate space-separated AND clause in Fabric."""
    tokens = clause.strip().split()
    if not tokens:
        return True

    results: list[bool | None] = []
    for tok in tokens:
        parsed = _parse_fabric_single_predicate(tok)
        if parsed is None:
            return None
        op, v_str = parsed
        res = _eval_fabric_atomic(op, v_str, target_str, target)
        if res is False:
            return False
        results.append(res)

    if any(r is None for r in results):
        return None
    return True


def _split_fabric_or_branches(raw: str) -> list[str]:
    """Split Fabric OR branches: JSON list, '||' separated, or single clause."""
    raw_clean = raw.strip()
    if raw_clean.startswith("[") and raw_clean.endswith("]"):
        try:
            arr = json.loads(raw_clean)
            if isinstance(arr, list):
                return [str(item).strip() for item in arr if str(item).strip()]
        except json.JSONDecodeError:
            pass

    if "||" in raw_clean:
        return [b.strip() for b in raw_clean.split("||") if b.strip()]

    return [raw_clean]


# =========================================================================
# Maven Range Parser and Evaluator
# =========================================================================

def _split_maven_ranges(raw: str) -> list[str]:
    """Split comma-separated Maven range intervals, respecting bracket pairs."""
    ranges: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in raw:
        if ch in "[(":
            depth += 1
            current.append(ch)
        elif ch in "])":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            part = "".join(current).strip()
            if part:
                ranges.append(part)
            current = []
        else:
            current.append(ch)
    final_part = "".join(current).strip()
    if final_part:
        ranges.append(final_part)
    return ranges


_MAVEN_INTERVAL_RE = re.compile(r"^([\[\(])\s*([^,\]\)]*)\s*,\s*([^,\]\)]*)\s*([\]\)])$")
_MAVEN_EXACT_RE = re.compile(r"^\[([^,\]\)]+)\]$")


def _eval_maven_single_interval(interval_str: str, target_str: str) -> bool | None:
    """Evaluate a single Maven interval against target version using Maven ComparableVersion ordering."""
    s = interval_str.strip()
    if not s or s == "*":
        return True

    if not target_str or not target_str.strip():
        return None

    # Exact match [1.0]
    m_exact = _MAVEN_EXACT_RE.match(s)
    if m_exact:
        exact_v = m_exact.group(1).strip()
        return compare_maven_versions(target_str, exact_v) == 0

    # Interval [lower, upper)
    m_int = _MAVEN_INTERVAL_RE.match(s)
    if m_int:
        left_bracket, lower_str, upper_str, right_bracket = m_int.groups()
        lower_str = lower_str.strip()
        upper_str = upper_str.strip()

        if lower_str and upper_str:
            if compare_maven_versions(lower_str, upper_str) > 0:
                return None

        if lower_str:
            cmp_low = compare_maven_versions(target_str, lower_str)
            if left_bracket == "[" and cmp_low < 0:
                return False
            if left_bracket == "(" and cmp_low <= 0:
                return False

        if upper_str:
            cmp_high = compare_maven_versions(target_str, upper_str)
            if right_bracket == "]" and cmp_high > 0:
                return False
            if right_bracket == ")" and cmp_high >= 0:
                return False

        return True

    # Bare version e.g. "1.0": soft/recommended version in Maven
    # matches() returns True for an equal version and None otherwise (never a definite False)
    if compare_maven_versions(target_str, s) == 0:
        return True
    return None


@dataclass(frozen=True)
class VersionRange:
    """Immutable version range specification with tri-state matching."""

    raw: str
    syntax: str  # "fabric" or "maven"
    parsed: Any = None
    is_valid: bool = True

    @classmethod
    def parse(cls, raw: str, syntax: str) -> VersionRange:
        """Parse raw range into a VersionRange instance."""
        if not raw or not raw.strip():
            return cls(raw=raw, syntax=syntax, parsed=None, is_valid=True)

        syntax_clean = syntax.lower()
        if syntax_clean not in ("fabric", "maven"):
            return cls(raw=raw, syntax=syntax, parsed=None, is_valid=False)

        raw_str = raw.strip()
        if syntax_clean == "fabric":
            branches = _split_fabric_or_branches(raw_str)
            if not branches:
                return cls(raw=raw, syntax=syntax, parsed=None, is_valid=True)

            parsed_branches = []
            for b in branches:
                clauses = b.split()
                branch_tokens = []
                for clause in clauses:
                    p = _parse_fabric_single_predicate(clause)
                    if p is None:
                        return cls(raw=raw, syntax=syntax, parsed=None, is_valid=False)
                    branch_tokens.append(p)
                parsed_branches.append(branch_tokens)

            return cls(raw=raw, syntax=syntax, parsed=parsed_branches, is_valid=True)

        # Maven syntax validation
        intervals = _split_maven_ranges(raw_str)
        if not intervals:
            return cls(raw=raw, syntax=syntax, parsed=None, is_valid=False)
        for interval in intervals:
            if interval == "*":
                continue
            if _MAVEN_EXACT_RE.match(interval):
                continue
            m_int = _MAVEN_INTERVAL_RE.match(interval)
            if m_int:
                left_bracket, lower_str, upper_str, right_bracket = m_int.groups()
                lower_str = lower_str.strip()
                upper_str = upper_str.strip()
                if lower_str and upper_str:
                    # If lower > upper, range is invalid
                    if compare_maven_versions(lower_str, upper_str) > 0:
                        return cls(raw=raw, syntax=syntax, parsed=None, is_valid=False)
                    # If lower == upper with exclusive bounds, invalid
                    if compare_maven_versions(lower_str, upper_str) == 0 and (left_bracket == "(" or right_bracket == ")"):
                        return cls(raw=raw, syntax=syntax, parsed=None, is_valid=False)
                continue
            # Bare version is valid if it tokenizes; a stray bracket means a malformed interval
            if not any(ch in interval for ch in "[]()") and tokenize_maven_version(interval):
                continue
            return cls(raw=raw, syntax=syntax, parsed=None, is_valid=False)

        return cls(raw=raw, syntax=syntax, parsed=intervals, is_valid=True)

    def matches(self, version: str) -> bool | None:
        """Evaluate whether target version satisfies range (True/False/None).

        Returns None if target version cannot be compared or range is invalid.
        """
        if not self.is_valid:
            return None

        if not version or not version.strip():
            return None

        v_clean = version.strip()

        # Any-version predicate matches every version, comparable or not
        if not self.raw or not self.raw.strip() or self.raw.strip() == "*":
            return True

        if self.syntax == "fabric":
            branches = _split_fabric_or_branches(self.raw)
            if not branches:
                return True

            target = ComparableVersion.parse(v_clean)
            has_none = False
            for branch in branches:
                res = _eval_fabric_and_clause(branch, v_clean, target)
                if res is True:
                    return True
                if res is None:
                    has_none = True

            if has_none:
                return None
            return False

        if self.syntax == "maven":
            intervals = _split_maven_ranges(self.raw)
            if not intervals:
                return True
            has_none = False
            for inter in intervals:
                res = _eval_maven_single_interval(inter, v_clean)
                if res is True:
                    return True
                if res is None:
                    has_none = True
            if has_none:
                return None
            return False

        return None

    def extract_exact_version(self) -> str | None:
        """Extract exact version string if pinned by this range, else None.

        Bare Maven versions do not pin an exact version.
        """
        if not self.is_valid or not self.raw:
            return None

        s = self.raw.strip()
        if self.syntax == "fabric":
            branches = _split_fabric_or_branches(s)
            if len(branches) == 1:
                b = branches[0]
                if b.startswith("="):
                    val = b[1:].strip()
                    if ComparableVersion.parse(val) is not None and not any(w in val for w in ("*", "x", "X")):
                        return val
                elif not any(b.startswith(op) for op in (">=", "<=", ">", "<", "~", "^")) and not any(
                    w in b for w in ("*", "x", "X", " ")
                ):
                    if ComparableVersion.parse(b) is not None:
                        return b
        elif self.syntax == "maven":
            # [1.0]
            m_exact = _MAVEN_EXACT_RE.match(s)
            if m_exact:
                return m_exact.group(1).strip()
            # [1.20.1,1.20.1]
            m_int = _MAVEN_INTERVAL_RE.match(s)
            if m_int:
                lb, low, high, rb = m_int.groups()
                low = low.strip()
                high = high.strip()
                if lb == "[" and rb == "]" and low and high:
                    if compare_maven_versions(low, high) == 0:
                        return low
        return None

    def to_dict(self) -> dict[str, Any]:
        """Deterministic dictionary serialization."""
        return {
            "is_valid": self.is_valid,
            "parsed": self.parsed,
            "raw": self.raw,
            "syntax": self.syntax,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VersionRange:
        """Construct from dictionary."""
        return cls(
            raw=data.get("raw", ""),
            syntax=data.get("syntax", "fabric"),
            parsed=data.get("parsed"),
            is_valid=data.get("is_valid", True),
        )
