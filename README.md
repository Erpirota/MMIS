# Minecraft Mod Intelligence Studio (MMIS)

Offline desktop application and command-line tool to scan, diagnose, analyze, and manage Minecraft modpacks locally without network access.

## What MMIS Is

MMIS is an offline diagnostic workbench designed for Minecraft players, modpack authors, and server administrators. It inspects local modpack installations, parses mod metadata across multiple loaders, detects conflicts and missing dependencies, diagnoses crash reports, tracks profile snapshot history, and generates standalone diagnostic reports—all completely offline.

## Features

- **Metadata Scanner**: Inspects Fabric (`fabric.mod.json`), Quilt (`quilt.mod.json`), Forge and NeoForge (`META-INF/mods.toml`, `META-INF/neoforge.mods.toml`, `META-INF/MANIFEST.MF`), including nested jars (JiJ) and pack manifests (CurseForge `manifest.json`, Modrinth `modrinth.index.json`, MultiMC `mmc-pack.json`).
- **Dependency & Conflict Analysis**: Evaluates required and optional dependencies, provider resolution, circular dependency cycles (SCCs), duplicate mod IDs, declared breaks/conflicts, and loader/Minecraft version mismatches with explicit certainty ratings (`Certain`, `Possible`, `Insufficient data`).
- **Profiles & History**: Configure modpack folders, take timestamped scan snapshots, view history, and compare/diff scans across versions.
- **Crash Log Diagnosis**: Analyzes crash reports, `latest.log`, and JVM `hs_err` logs (the first 20 MB of each file). Identifies root causes, stack frames, mixin failures, loader exceptions, and ranked suspect mods.
- **Reports**: Generates self-contained HTML (dark theme, print styling, strict CSP) and machine-readable JSON diagnostic reports.
- **Backup & Restore**: Managed full application backup archives (`.zip`) with SHA-256 manifest verification and safety backups before restore.
- **Desktop Graphical Interface**: Dedicated pages for Dashboard, Profiles, Mods, Issues, Compare, Crash Logs, Reports, and Backup.
- **Command-Line Interface (CLI)**: Scan, analysis, profile, crash log, report, and backup commands for scripting and headless use.

## Requirements

- **Packaged Standalone Build**: 64-bit Windows. No Python or external dependencies required.
- **Development**: 64-bit Windows, Python >= 3.14.

MMIS has been developed and tested on Windows 11 only.

## Installation

For development and running from source:

```powershell
# Create and activate virtual environment
py -3.14 -m venv .venv

# Install project with development dependencies
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

`requirements-lock.txt` records the exact versions the project was developed and tested with; install it first (`pip install -r requirements-lock.txt`) to reproduce that environment.

## Running in Development

### Desktop GUI

```powershell
.\.venv\Scripts\python.exe -m mmis gui [--data-dir <path>]
```

### CLI

Scan a modpack directory (human-readable summary):

```powershell
.\.venv\Scripts\python.exe -m mmis scan <modpack-dir>
```

Output deterministic sorted JSON:

```powershell
.\.venv\Scripts\python.exe -m mmis scan <modpack-dir> --json
```

#### Profiles

Manage modpack profiles and snapshot history:

```powershell
# Add a new profile
.\.venv\Scripts\python.exe -m mmis profile add "MyPack" C:\path\to\modpack

# List configured profiles with derived status
.\.venv\Scripts\python.exe -m mmis profile list

# Scan a profile and store a snapshot
.\.venv\Scripts\python.exe -m mmis profile scan "MyPack" --label "v1.0"

# View scan history
.\.venv\Scripts\python.exe -m mmis profile history "MyPack"

# Diff scans (default: previous vs latest)
.\.venv\Scripts\python.exe -m mmis profile diff "MyPack"

# Pin/unpin a scan to protect from retention pruning
.\.venv\Scripts\python.exe -m mmis profile pin "MyPack" 1

# Rename or remove profile
.\.venv\Scripts\python.exe -m mmis profile rename "MyPack" "MyPack2"
.\.venv\Scripts\python.exe -m mmis profile remove "MyPack2"
```

#### Analysis

Analyze modpack dependencies and conflicts offline:

```powershell
# Run dependency and conflict analyzers on a modpack directory
.\.venv\Scripts\python.exe -m mmis analyze <modpack-dir>

# Deterministic JSON analysis report
.\.venv\Scripts\python.exe -m mmis analyze <modpack-dir> --json
```

#### Crash Logs

Analyze Minecraft crash reports and logs:

```powershell
# Analyze a crash report with optional pack/profile attribution
.\.venv\Scripts\python.exe -m mmis crash analyze <file> [--pack <dir> | --profile <name>] [--save] [--json]

# Manage imported crash reports
.\.venv\Scripts\python.exe -m mmis crash list [--profile <name>] [--json]
.\.venv\Scripts\python.exe -m mmis crash show <id> [--json]
.\.venv\Scripts\python.exe -m mmis crash delete <id>
```

#### Reports

Export diagnostic reports in HTML or JSON format:

```powershell
# Export diagnostic report as standalone HTML
.\.venv\Scripts\python.exe -m mmis report "MyPack" --format html --out report.html [--force]

# Export diagnostic report as JSON
.\.venv\Scripts\python.exe -m mmis report "MyPack" --format json --out report.json [--force]

# Export a single scan snapshot and compare against it
.\.venv\Scripts\python.exe -m mmis profile export-scan "MyPack" 1 --out snapshot.json [--force]
.\.venv\Scripts\python.exe -m mmis profile diff "MyPack" 2 --file snapshot.json
```

#### Backup & Restore

Manage application database and snapshot backups:

```powershell
# Create a backup archive
.\.venv\Scripts\python.exe -m mmis backup create --out backup.zip [--force]

# Verify backup integrity and inspect manifest
.\.venv\Scripts\python.exe -m mmis backup verify backup.zip

# Restore from a backup archive (creates an automatic safety backup before replacement)
.\.venv\Scripts\python.exe -m mmis backup restore backup.zip --yes
```

## Tests

Execute test suites:

```powershell
# Run the whole suite (UI tests use Qt's offscreen platform, no window is opened)
.\.venv\Scripts\python.exe -m pytest

# Run only the UI tests
.\.venv\Scripts\python.exe -m pytest tests/ui
```

`scripts/master_checks/` holds independent acceptance scripts, one per milestone, that exercise the application from the outside (CLI, native windows, the packaged build). Each script documents its own usage in its first lines.

## Building the Windows Package

Create the standalone onedir Windows distribution:

```powershell
.\.venv\Scripts\python.exe scripts\build_package.py
```

### Output Layout

The build produces a single distribution directory at `dist/MMIS/` containing two executables sharing runtime files:
- `dist/MMIS/MMIS.exe`: Desktop windowed graphical application (`console=False`), accepts `--data-dir DIR`.
- `dist/MMIS/mmis-cli.exe`: Standalone command-line interface with full subcommand support.
- `dist/MMIS/_internal/`: Bundled Python runtime, Qt libraries, and UI assets.

### Running the Packaged Executables

```powershell
# Run desktop GUI
.\dist\MMIS\MMIS.exe

# Run desktop GUI with custom data directory
.\dist\MMIS\MMIS.exe --data-dir "C:\Path\To\Data"

# Run CLI commands
.\dist\MMIS\mmis-cli.exe --help
.\dist\MMIS\mmis-cli.exe scan "C:\Path\To\Modpack"
```

## Project Structure

- `src/mmis/domain/`: Pure dataclasses, enums, version range solvers, and diagnostic finding definitions.
- `src/mmis/analysis/`: Diagnostic engine, dependency graphs, cycle detection, and conflict solvers.
- `src/mmis/minecraft/`: Jar zip reading, metadata parsers, nested jar handling, and manifest detection.
- `src/mmis/storage/`: SQLite schema, repository queries, and snapshot serialization.
- `src/mmis/services/`: Application services for profiles, backups, reporting, settings, and crash diagnosis.
- `src/mmis/cli.py`: Command-line interface subcommands and argument parsers.
- `src/mmis/ui/`: PySide6 graphical interface pages, widgets, themes, and background threadpool workers.
- `tests/`: Automated unit, integration, boundary, and UI test suites.
- `scripts/`: Development utility scripts, demo seeding, and package build orchestration.
- `packaging/`: PyInstaller specification (`mmis.spec`) and standalone executable launcher entry points.

## Local Data

MMIS stores configuration, profile records, and scan snapshots locally. The data directory is resolved in the following priority order:
1. Explicit `--data-dir <path>` command-line option.
2. `MMIS_DATA_DIR` environment variable.
3. Default user directory: `%APPDATA%\MMIS` on Windows.

Files located within the data directory:
- `mmis.db`: SQLite database storing profiles, scan snapshots, and crash records.
- `app_settings.json`: Persistent user interface settings (window size and position, sidebar state, last profile and page).
- `backups/`: Managed folder for the backup archives created from the Backup page.
- `mmis_pre_restore_<timestamp>.zip`: Safety backups written automatically before a restore.
- `mmis_app.log`: Rotating log file of the desktop application.

## Backups

- Backup creation compiles `mmis.db` and `app_settings.json` into a compressed zip archive with a SHA-256 manifest.
- Archive verification checks zip structure and recalculates checksums against the manifest.
- Restoring from an archive automatically generates a timestamped pre-restore safety archive (`mmis_pre_restore_<timestamp>.zip`) before replacing existing data.
- The user interface intentionally does not provide a backup deletion button to safeguard data history; archive removal is handled by direct filesystem deletion.

## Crash Logs

- Analyzes crash reports, `latest.log`, and JVM `hs_err` files. At most the first 20 MB of a file are analyzed (`MAX_LOG_SIZE`); a longer file is truncated at that bound and the analysis says so with a `CRASH_TRUNCATED` finding.
- MMIS does **not** store the original crash log file on disk; only the structured diagnostic analysis, stack frame summaries, and attributed evidence lines are persisted.

## Extending MMIS

MMIS provides a simple, direct analyzer extension point in `src/mmis/analysis/engine.py`:
- `ANALYZERS: list[AnalyzerFunc]`: A list of callable functions with the signature `(ScanResult) -> list[Finding]`.
- Custom analyzers can be added to this list in Python code; all returned findings are aggregated, sorted deterministically, and included in severity, certainty, and category metrics.
- There is deliberately no runtime plugin loader, no plugin marketplace, and no remote extension registry.

## Known Limits

- **Metadata Size Limits**: Metadata entries (`fabric.mod.json`, `quilt.mod.json`, `mods.toml`, `MANIFEST.MF`) and pack manifests (`manifest.json`, `modrinth.index.json`, `mmc-pack.json`) exceeding 4 MiB (4,194,304 bytes) are rejected with `METADATA_TOO_LARGE`.
- **Nested Jar Limits**: Nested jars (Jar-in-Jar) are parsed up to a maximum depth of 3 levels and a maximum decompressed size of 64 MiB per nested entry (`NESTED_JAR_DEPTH_EXCEEDED`, `NESTED_JAR_SIZE_EXCEEDED`).
- **Legacy Mod Info**: Legacy Forge `mcmod.info` files are recognized (`LEGACY_MOD_INFO`) but not fully parsed.
- **Archive Verification**: Every jar file is verified with a complete CRC-32 check (`zipfile.testzip()`); exceptionally large or heavily compressed jar files require proportional scan time.
- **Platform Dependencies**: Dependencies on the platform itself (such as `minecraft`, `java`, `fabricloader`, `quilt_loader`, `forge`, `neoforge`) are reported as `PLATFORM_DEP_UNVERIFIED`: MMIS does not inspect the installed game, loader, or Java runtime.
- **Operating System**: Developed and tested on Windows 11 only.

## What v1 Does NOT Do

- No network access: MMIS operates 100% offline; no external requests are made.
- No mod downloading or updating: MMIS is an intelligence and diagnostic tool, not a mod launcher or package manager.
- No automatic pack modifications: MMIS does not alter jar files, modify configs, or silently repair packs.
- No editing of mod files: Files are opened strictly read-only.
- No Settings page: The few persisted preferences live in `app_settings.json`; there is no UI to edit them.
- No backup deletion from the UI: Backup files must be removed manually from the filesystem.
- No storage of original raw crash logs: Only structured diagnoses and evidence lines are retained.
- No dynamic plugin system: Analyzers are configured directly via Python functions.
