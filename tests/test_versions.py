"""Table-driven tests for Fabric semver predicates and Maven version ranges.

Covers at least 60 test cases verifying operators, wildcards, AND/OR logic,
prerelease ordering, build metadata, tri-state matching, and Maven ComparableVersion rules.
"""

import pytest
from mmis.domain.versions import (
    ComparableVersion,
    VersionRange,
    compare_maven_versions,
    tokenize_maven_version,
)

# Table-driven test cases: (raw_range, syntax, target_version, expected_result)
CASES = [
    # --- Fabric exact match ---
    ("1.20.1", "fabric", "1.20.1", True),
    ("=1.20.1", "fabric", "1.20.1", True),
    ("=1.20.1", "fabric", "1.20.2", False),
    ("=1.20.1", "fabric", "1.20.0", False),
    # --- Fabric comparison operators ---
    (">1.20", "fabric", "1.20.1", True),
    (">1.20.1", "fabric", "1.20.1", False),
    (">=1.20.1", "fabric", "1.20.1", True),
    (">=1.20.1", "fabric", "1.20.2", True),
    (">=1.20.1", "fabric", "1.20.0", False),
    ("<1.20.1", "fabric", "1.20.0", True),
    ("<1.20.1", "fabric", "1.20.1", False),
    ("<=1.20.1", "fabric", "1.20.1", True),
    ("<=1.20.1", "fabric", "1.20.2", False),
    # --- Fabric wildcards ---
    ("*", "fabric", "1.20.1", True),
    ("*", "fabric", "2.0.0", True),
    ("*", "fabric", "24w14a", True),  # * matches any version, comparable or not
    ("1.20.x", "fabric", "1.20.1", True),
    ("1.20.x", "fabric", "1.20.0", True),
    ("1.20.x", "fabric", "1.21.0", False),
    ("1.20.*", "fabric", "1.20.4", True),
    ("1.20.*", "fabric", "1.19.4", False),
    ("1.x", "fabric", "1.19.2", True),
    ("1.x", "fabric", "2.0.0", False),
    # --- Fabric tilde ~ ---
    ("~1.2.3", "fabric", "1.2.3", True),
    ("~1.2.3", "fabric", "1.2.4", True),
    ("~1.2.3", "fabric", "1.3.0", False),
    ("~1.2.3", "fabric", "1.2.2", False),
    ("~1.2", "fabric", "1.2.0", True),
    ("~1.2", "fabric", "1.2.9", True),
    ("~1.2", "fabric", "1.3.0", False),
    # --- Fabric caret ^ (fabric-loader semantics: >= given AND same major) ---
    ("^1.2.3", "fabric", "1.2.3", True),
    ("^1.2.3", "fabric", "1.9.0", True),
    ("^1.2.3", "fabric", "2.0.0", False),
    ("^0.2.3", "fabric", "0.2.3", True),
    ("^0.2.3", "fabric", "0.2.9", True),
    ("^0.2.3", "fabric", "0.9.0", True),
    ("^0.2.3", "fabric", "1.0.0", False),
    ("^0.0.3", "fabric", "0.0.3", True),
    ("^0.0.3", "fabric", "0.0.4", True),
    ("^0.0.3", "fabric", "1.0.0", False),
    # --- Fabric composite space = AND ---
    (">=1.20 <=1.20.4", "fabric", "1.20.1", True),
    (">=1.20 <=1.20.4", "fabric", "1.20.4", True),
    (">=1.20 <=1.20.4", "fabric", "1.20.5", False),
    (">=1.20 <=1.20.4", "fabric", "1.19.4", False),
    (">1.20 <1.20.4", "fabric", "1.20.0", False),
    # --- Fabric list/branches = OR ---
    ('["1.19.2", "1.20.1"]', "fabric", "1.19.2", True),
    ('["1.19.2", "1.20.1"]', "fabric", "1.20.1", True),
    ('["1.19.2", "1.20.1"]', "fabric", "1.20.2", False),
    ("1.19.x || 1.20.x", "fabric", "1.19.4", True),
    ("1.19.x || 1.20.x", "fabric", "1.20.1", True),
    ("1.19.x || 1.20.x", "fabric", "1.21.0", False),
    # --- Prerelease ordering (SemVer for Fabric) ---
    ("<1.0.0", "fabric", "1.0.0-rc.1", True),
    (">=1.0.0", "fabric", "1.0.0-rc.1", False),
    (">1.0.0-alpha", "fabric", "1.0.0-alpha.1", True),
    (">1.0.0-alpha.1", "fabric", "1.0.0-alpha.beta", True),
    (">1.0.0-alpha.beta", "fabric", "1.0.0-beta", True),
    (">1.0.0-beta", "fabric", "1.0.0-beta.2", True),
    (">1.0.0-beta.2", "fabric", "1.0.0-beta.11", True),
    (">1.0.0-beta.11", "fabric", "1.0.0-rc.1", True),
    ("<1.0.0-beta.2", "fabric", "1.0.0-beta.11", False),
    # --- Build metadata ---
    ("=1.20.1", "fabric", "1.20.1+build.1", True),
    (">=1.20.1", "fabric", "1.20.1+build.42", True),
    ("=1.20.1+build.5", "fabric", "1.20.1+build.99", True),
    # --- Maven ranges (Master cases) ---
    ("[1.20.1,)", "maven", "1.20.1-47.2.0", True),
    ("[0.5.1,)", "maven", "0.5.1.f", True),
    ("[1.0,)", "maven", "1.0-SNAPSHOT", False),
    ("[2.0,1.0]", "maven", "1.5.0", None),  # invalid lower > upper
    ("1.0", "maven", "1.0", True),  # soft requirement equal
    ("1.0", "maven", "1.2", None),  # soft requirement not equal -> None
    ("1.0", "maven", "0.9", None),
    # --- Standard Maven ranges ---
    ("[1.2,)", "maven", "1.2.0", True),
    ("[1.2,)", "maven", "1.3.0", True),
    ("[1.2,)", "maven", "1.1.9", False),
    ("[1.0,2.0)", "maven", "1.0.0", True),
    ("[1.0,2.0)", "maven", "1.5.0", True),
    ("[1.0,2.0)", "maven", "2.0.0", False),
    ("(,1.5]", "maven", "1.5.0", True),
    ("(,1.5]", "maven", "1.0.0", True),
    ("(,1.5]", "maven", "1.5.1", False),
    ("[1.0]", "maven", "1.0.0", True),
    ("[1.0]", "maven", "1.0.1", False),
    ("[1.0,2.0),[3.0,4.0)", "maven", "1.5.0", True),
    ("[1.0,2.0),[3.0,4.0)", "maven", "3.5.0", True),
    ("[1.0,2.0),[3.0,4.0)", "maven", "2.5.0", False),
    ("", "maven", "1.20.1", True),
    ("*", "maven", "1.20.1", True),
    ("*", "maven", "24w14a", True),
    # --- Additional Maven ComparableVersion cases (>= 15 cases) ---
    ("[1.0,)", "maven", "1.0.0", True),  # trailing zero equality
    ("[1.0.0,)", "maven", "1.0", True),
    ("(,1.0-beta)", "maven", "1.0-alpha", True),  # alpha < beta
    ("(,1.0-beta)", "maven", "1.0-a", True),  # a alias for alpha
    ("[1.0-b,)", "maven", "1.0-beta", True),  # b alias for beta
    ("(,1.0-rc)", "maven", "1.0-milestone", True),  # milestone < rc
    ("(,1.0-rc)", "maven", "1.0-m", True),  # m alias for milestone
    ("[1.0-cr,)", "maven", "1.0-rc", True),  # cr alias for rc
    ("(,1.0-snapshot)", "maven", "1.0-rc", True),  # rc < snapshot
    ("[1.0,)", "maven", "1.0-sp", True),  # release < sp
    ("[1.0-sp,)", "maven", "1.0-patch", True),  # sp < other strings
    ("[1.0-patch,)", "maven", "1.0-1", True),  # other strings < numbers
    ("[1.0-1,)", "maven", "1.0-2", True),  # number ordering
    ("[1.2.3,)", "maven", "1.2.3.1", True),
    ("[1.0, 1.5]", "maven", "1.0", True),
    ("[1.0, 1.5]", "maven", "1.5", True),
    ("[1.0, 1.5]", "maven", "1.5.1", False),
    ("(1.0, 1.5)", "maven", "1.0", False),
    ("(1.0, 1.5)", "maven", "1.5", False),
    ("(1.0, 1.5)", "maven", "1.2", True),
    # --- Tri-state None for non-comparable / invalid ---
    (">=1.20", "fabric", "24w14a", None),
    ("=1.20", "fabric", "1.14_combat-3", None),
    (">=1.20", "fabric", "", None),
    ("invalid_syntax <<>>", "fabric", "1.20.1", None),
]


def test_cases_count():
    """Ensure at least 60 cases are evaluated."""
    assert len(CASES) >= 60


@pytest.mark.parametrize("raw_range,syntax,target,expected", CASES)
def test_version_range_matching(raw_range: str, syntax: str, target: str, expected: bool | None):
    vr = VersionRange.parse(raw_range, syntax=syntax)
    assert vr.matches(target) == expected


def test_maven_exact_version_extraction():
    # [1.20.1]
    assert VersionRange.parse("[1.20.1]", syntax="maven").extract_exact_version() == "1.20.1"
    # [1.20.1,1.20.1]
    assert VersionRange.parse("[1.20.1,1.20.1]", syntax="maven").extract_exact_version() == "1.20.1"
    # bare "1.0" does NOT pin an exact version
    assert VersionRange.parse("1.0", syntax="maven").extract_exact_version() is None
    # [1.0, 2.0) does NOT pin an exact version
    assert VersionRange.parse("[1.0, 2.0)", syntax="maven").extract_exact_version() is None


def test_maven_version_ordering_direct():
    assert compare_maven_versions("1.0", "1.0.0") == 0
    assert compare_maven_versions("1.0-alpha", "1.0-beta") < 0
    assert compare_maven_versions("1.0-a", "1.0-alpha") == 0
    assert compare_maven_versions("1.0-beta", "1.0-milestone") < 0
    assert compare_maven_versions("1.0-milestone", "1.0-rc") < 0
    assert compare_maven_versions("1.0-rc", "1.0-snapshot") < 0
    assert compare_maven_versions("1.0-snapshot", "1.0") < 0
    assert compare_maven_versions("1.0", "1.0-sp") < 0
    assert compare_maven_versions("1.0-sp", "1.0-other") < 0
    assert compare_maven_versions("1.0-other", "1.0-1") < 0
    assert compare_maven_versions("1.20.1-47.2.0", "1.20.1") > 0
    assert compare_maven_versions("0.5.1.f", "0.5.1") > 0


def test_comparable_version_parsing_edge_cases():
    assert ComparableVersion.parse("not-a-version") is None
    assert ComparableVersion.parse("24w14a") is None
    assert ComparableVersion.parse("") is None

    v = ComparableVersion.parse("v1.20.1-rc.1+build.42")
    assert v is not None
    assert v.components == (1, 20, 1)
    assert v.prerelease == ("rc", 1)
    assert v.build == ("build", "42")


@pytest.mark.parametrize("raw", ["[1.0", "1.0]", "(1.0,2.0", "1.0,2.0)"])
def test_maven_unbalanced_bracket_is_invalid(raw):
    rng = VersionRange.parse(raw, "maven")
    assert rng.is_valid is False
    assert rng.matches("1.0") is None
