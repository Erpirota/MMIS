"""Tests for crash and log analyzer covering Acceptance Criteria A3, A4, A5, A6, and performance."""

from pathlib import Path
import time
import pytest

from mmis.analysis.crashlog import analyze_crash
from mmis.domain.crash import CrashReportKind
from mmis.domain.findings import (
    CRASH_CLASS_NOT_FOUND,
    CRASH_GENERIC_EXCEPTION,
    CRASH_JAVA_VERSION_MISMATCH,
    CRASH_LOADER_EXPLICIT_ISSUE,
    CRASH_MIXIN_FAILURE,
    CRASH_NATIVE_JVM,
    CRASH_NO_EXCEPTION_FOUND,
    CRASH_NO_SUCH_METHOD_FIELD,
    CRASH_OOM,
    CRASH_STACK_OVERFLOW,
    CRASH_TRUNCATED,
    get_category,
)
from mmis.domain.models import Certainty, JarFile, Loader, ModInfo, ScanResult, Severity

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "crash"


def _read_fixture(name: str) -> str:
    path = FIXTURES_DIR / name
    return path.read_text(encoding="utf-8")


# --- A4: All 10 fixtures parsed with expected attributes ---

def test_fixture_vanilla_crash():
    content = _read_fixture("vanilla_crash.txt")
    result = analyze_crash(content, source_name="vanilla_crash.txt")

    assert result.kind == CrashReportKind.CRASH_REPORT
    assert result.description == "Initializing game"
    assert result.mc_version == "1.20.1"
    assert result.root_cause is not None
    assert result.root_cause.exception_class == "java.lang.NullPointerException"
    assert len(result.root_cause.frames) > 0
    assert result.root_cause.frames[0].class_name == "net.minecraft.client.Minecraft"
    assert result.root_cause.frames[0].method_name == "init"
    assert result.root_cause.frames[0].line_number == 540


def test_fixture_forge_crash():
    content = _read_fixture("forge_crash.txt")
    result = analyze_crash(content, source_name="forge_crash.txt")

    assert result.kind == CrashReportKind.CRASH_REPORT
    assert result.description == "Unexpected error"
    assert result.mc_version == "1.20.1"
    assert result.detected_loader == "forge"
    assert result.root_cause is not None
    assert result.root_cause.exception_class == "java.lang.NullPointerException"
    assert len(result.mods_listed) >= 2

    # Verify mod table parsing
    mod_ids = [m.id for m in result.mods_listed]
    assert "shinyblocks" in mod_ids
    assert "fastrender" in mod_ids

    # Suspect attribution: shinyblocks (frame_jar, rank 1), fastrender (mixin, rank 2)
    assert len(result.suspects) >= 2
    assert result.suspects[0].mod_id == "shinyblocks"
    assert result.suspects[0].attribution == "frame_jar"
    assert result.suspects[0].certainty == Certainty.POSSIBLE
    assert result.suspects[0].rank == 2
    assert len(result.suspects[0].evidence_lines) >= 3

    assert result.suspects[1].mod_id == "fastrender"
    assert result.suspects[1].attribution == "mixin"
    assert result.suspects[1].certainty == Certainty.POSSIBLE
    assert result.suspects[1].rank == 3
    assert result.suspects[1].rank > result.suspects[0].rank  # lower rank

    # Frame annotation parsing checks
    top_exc = result.exceptions[0]
    frame_shiny = top_exc.frames[0]
    assert frame_shiny.jar_file == "shinyblocks-2.4.1.jar"
    assert frame_shiny.jar_version == "2.4.1"
    assert frame_shiny.mixin_info is None

    frame_model = top_exc.frames[1]
    assert frame_model.jar_file == "client-1.20.1-20230612.114412-srg.jar"
    assert frame_model.jar_version is None
    assert "fastrender.mixins.json" in frame_model.mixin_info
    assert "ModelBakeryMixin" in frame_model.mixin_info


def test_fixture_fabric_crash():
    content = _read_fixture("fabric_crash.txt")
    result = analyze_crash(content, source_name="fabric_crash.txt")

    assert result.kind == CrashReportKind.CRASH_REPORT
    assert result.root_cause is not None
    assert result.root_cause.exception_class == "java.lang.ClassNotFoundException"
    assert len(result.mods_listed) >= 1
    assert any(m.id == "mymod" for m in result.mods_listed)
    assert any(f.code == CRASH_CLASS_NOT_FOUND for f in result.findings)


def test_fixture_fabric_loader_resolution_error():
    content = _read_fixture("fabric_loader_resolution_error.txt")
    result = analyze_crash(content, source_name="fabric_loader_resolution_error.txt")

    assert result.mc_version == "1.20.1"
    assert result.detected_loader == "fabric"

    loader_findings = [f for f in result.findings if f.code == CRASH_LOADER_EXPLICIT_ISSUE]
    assert len(loader_findings) == 1
    f = loader_findings[0]
    assert f.certainty == Certainty.CERTAIN
    assert f.related_mod_ids == ["lithium", "fabric-api"]
    assert "Install fabric-api, version 0.90.0 or later." in f.suggestion

    assert result.top_suspect is not None
    assert result.top_suspect.certainty == Certainty.CERTAIN
    assert result.top_suspect.rank == 1
    assert result.top_suspect.mod_id == "lithium"
    assert result.top_suspect.attribution == "loader_statement"


def test_fixture_mixin_failure():
    content = _read_fixture("mixin_failure.txt")
    result = analyze_crash(content, source_name="mixin_failure.txt")

    assert result.kind == CrashReportKind.CRASH_REPORT
    assert result.root_cause is not None
    assert "InjectionError" in result.root_cause.exception_class
    assert any(f.code == CRASH_MIXIN_FAILURE for f in result.findings)


def test_fixture_latest_log():
    content = _read_fixture("latest_log.log")
    result = analyze_crash(content, source_name="latest_log.log")

    assert result.kind == CrashReportKind.LOG_FILE
    assert result.detected_loader == "fabric"
    assert result.root_cause is not None
    assert result.root_cause.exception_class == "java.lang.OutOfMemoryError"
    assert any(f.code == CRASH_OOM for f in result.findings)


def test_fixture_hs_err():
    content = _read_fixture("hs_err_pid.log")
    result = analyze_crash(content, source_name="hs_err_pid.log")

    assert result.kind == CrashReportKind.HS_ERR
    assert any(f.code == CRASH_NATIVE_JVM for f in result.findings)


def test_fixture_plain_text_no_exception():
    content = _read_fixture("plain_text_no_exception.txt")
    result = analyze_crash(content, source_name="plain_text_no_exception.txt")

    assert result.root_cause is None
    assert len(result.exceptions) == 0
    assert any(f.code == CRASH_NO_EXCEPTION_FOUND for f in result.findings)
    assert result.top_suspect is None


def test_fixture_crlf_endings():
    content = _read_fixture("crlf_endings.txt")
    result = analyze_crash(content, source_name="crlf_endings.txt")

    assert result.kind == CrashReportKind.CRASH_REPORT
    assert result.root_cause is not None
    assert result.root_cause.exception_class == "java.lang.NullPointerException"
    assert len(result.root_cause.frames) == 2


def test_fixture_deep_caused_by_chain():
    content = _read_fixture("deep_caused_by.txt")
    result = analyze_crash(content, source_name="deep_caused_by.txt")

    assert result.root_cause is not None
    assert result.root_cause.exception_class == "java.lang.NullPointerException"
    assert result.root_cause.message == "Root cause NPE here"
    assert len(result.root_cause.frames) >= 1
    assert result.root_cause.frames[0].class_name == "com.example.deep.LeafClass"


def test_fixture_forge_missing_deps():
    content = _read_fixture("forge_missing_deps.log")
    result = analyze_crash(content, source_name="forge_missing_deps.log")

    assert result.mc_version == "1.20.1"
    assert result.detected_loader == "forge"

    loader_findings = [f for f in result.findings if f.code == CRASH_LOADER_EXPLICIT_ISSUE]
    assert len(loader_findings) == 2

    # First finding: geckolib requested by examplemod is missing
    f1 = loader_findings[0]
    assert f1.certainty == Certainty.CERTAIN
    assert set(f1.related_mod_ids) == {"examplemod", "geckolib"}
    assert "missing" in f1.message.lower()
    assert "geckolib" in f1.suggestion.lower()

    # Second finding: curios requested by othermod with wrong version
    f2 = loader_findings[1]
    assert f2.certainty == Certainty.CERTAIN
    assert set(f2.related_mod_ids) == {"othermod", "curios"}
    assert "5.1.0" in f2.message
    assert "5.4" in f2.message or "5.4" in f2.suggestion

    # Wrapper exception suppressed
    assert not any(f.code == CRASH_GENERIC_EXCEPTION for f in result.findings)


def test_fixture_fabric_loader_frames():
    content = _read_fixture("fabric_loader_frames.log")
    result = analyze_crash(content, source_name="fabric_loader_frames.log")

    # Platform jar fabric-loader must NEVER be a suspect
    assert len(result.suspects) == 0
    assert result.top_suspect is None

    # Finding related_mod_ids must never contain platform ids
    for f in result.findings:
        assert "fabric-loader" not in f.related_mod_ids
        assert "fabricloader" not in f.related_mod_ids
        assert "minecraft" not in f.related_mod_ids


# --- Robustness: Empty, Garbage, Binary ---

def test_robustness_empty_garbage_binary():
    # Empty
    res_empty = analyze_crash("", source_name="empty.txt")
    assert res_empty.kind == CrashReportKind.UNKNOWN
    assert any(f.code == CRASH_NO_EXCEPTION_FOUND for f in res_empty.findings)

    # Garbage
    res_garbage = analyze_crash("??? ### random string not a log %%%", source_name="garbage.txt")
    assert res_garbage.kind == CrashReportKind.UNKNOWN
    assert any(f.code == CRASH_NO_EXCEPTION_FOUND for f in res_garbage.findings)

    # Binary with invalid utf-8 bytes
    binary_data = b"\x80\x81\xff\xfe\x00\x01\x02random binary data \x00\xff"
    res_binary = analyze_crash(binary_data, source_name="binary.dat")
    assert res_binary.kind == CrashReportKind.UNKNOWN
    assert any(f.code == CRASH_NO_EXCEPTION_FOUND for f in res_binary.findings)


# --- A5: Suspects and ScanResult Mapping ---

def test_suspect_mapping_with_scan_result():
    log_text = (
        "java.lang.NullPointerException: Boom\n"
        "\tat com.example.ironchests.Tile.tick(Tile.java:10) ~[ironchests-1.20.1-14.4.4.jar:14.4.4]\n"
        "\tat net.minecraft.world.level.Level.tick(Level.java:100)\n"
    )
    mod = ModInfo(
        id="ironchests",
        name="Iron Chests",
        version="14.4.4",
        loaders=[Loader.FORGE],
        jar_path="mods/ironchests-1.20.1-14.4.4.jar",
    )
    jar = JarFile(
        path="mods/ironchests-1.20.1-14.4.4.jar",
        size_bytes=1000,
        sha256="123",
        mods=["ironchests"],
    )
    scan = ScanResult(scanned_path=".", mods_dir="mods", jars=[jar], mods=[mod])

    result = analyze_crash(log_text, scan=scan, source_name="test.log")
    assert result.top_suspect is not None
    assert result.top_suspect.mod_id == "ironchests"
    assert result.top_suspect.rank == 2
    assert result.top_suspect.attribution == "frame_jar"
    assert result.top_suspect.certainty == Certainty.POSSIBLE


def test_suspect_package_heuristic():
    log_text = (
        "java.lang.RuntimeException: Error\n"
        "\tat com.myuniquecompany.secretmod.Main.init(Main.java:20)\n"
    )
    mod = ModInfo(id="secretmod", version="1.0.0", jar_path="mods/secret.jar")
    scan = ScanResult(scanned_path=".", mods_dir="mods", mods=[mod])

    result = analyze_crash(log_text, scan=scan, source_name="test.log")
    assert result.top_suspect is not None
    assert result.top_suspect.rank in (3, 4)
    assert result.top_suspect.attribution == "package_heuristic"
    assert result.top_suspect.certainty == Certainty.INSUFFICIENT_DATA
    assert "heuristic" in result.top_suspect.reason.lower()


# --- A6: Signatures Table Tests ---

def test_signature_unsupported_class_version():
    log_text = (
        "java.lang.UnsupportedClassVersionError: com/example/mod/ModClass has been compiled by a more recent "
        "version of the Java Runtime (class file version 65.0), this version of the Java Runtime only recognizes "
        "class file versions up to 61.0\n"
        "\tat java.base/java.lang.ClassLoader.defineClass(ClassLoader.java:1017)\n"
    )
    result = analyze_crash(log_text)
    findings = [f for f in result.findings if f.code == CRASH_JAVA_VERSION_MISMATCH]
    assert len(findings) == 1
    assert "Java 21" in findings[0].message
    assert findings[0].suggestion is not None


def test_signature_stack_overflow():
    log_text = (
        "java.lang.StackOverflowError\n"
        "\tat com.example.mod.Recursive.loop(Recursive.java:15)\n"
        "\tat com.example.mod.Recursive.loop(Recursive.java:15)\n"
    )
    result = analyze_crash(log_text)
    assert any(f.code == CRASH_STACK_OVERFLOW for f in result.findings)


# --- A3: All findings have >= 1 Evidence, Category, and Suggestion ---

def test_crash_findings_evidence_and_suggestion():
    for f_name in (
        "vanilla_crash.txt",
        "forge_crash.txt",
        "fabric_crash.txt",
        "fabric_loader_resolution_error.txt",
        "mixin_failure.txt",
        "latest_log.log",
        "hs_err_pid.log",
        "plain_text_no_exception.txt",
        "forge_missing_deps.log",
        "fabric_loader_frames.log",
    ):
        content = _read_fixture(f_name)
        res = analyze_crash(content, source_name=f_name)
        assert len(res.findings) > 0
        for f in res.findings:
            assert len(f.evidence) >= 1
            assert get_category(f.code) == "crash"
            assert f.suggestion is not None
            assert len(f.suggestion.strip()) > 0


# --- Linear performance on ~5 MB log ---

def test_linear_performance_large_log(tmp_path: Path):
    # Generate ~5 MB log file
    line_sample = "[12:34:56] [main/INFO]: Regular log line processing block at tick 123456 with some data\n"
    repeat_count = 60000  # ~5 MB
    large_content = line_sample * repeat_count
    # Append exception at the end
    large_content += (
        "java.lang.NullPointerException: End of big log\n"
        "\tat com.example.big.BigLogTest.run(BigLogTest.java:100)\n"
    )

    t0 = time.perf_counter()
    result = analyze_crash(large_content, source_name="big.log")
    elapsed = time.perf_counter() - t0

    assert result.root_cause is not None
    assert result.root_cause.exception_class == "java.lang.NullPointerException"
    # Bound: 5 MB must be parsed in less than 5 seconds
    assert elapsed < 5.0, f"Analysis took {elapsed:.2f}s, expected < 5.0s"
