# Development

Setting up the source, the project layout, the tests and tools, and building the EXE and the release zip. Back to the [README](../README.md).

## Requirements

- Windows 10 or 11 (64-bit). The app uses Win32 calls for window discovery, DPI awareness and Efficiency mode.
- [Python 3.14](https://www.python.org/downloads/windows/) (64-bit). With the Python install manager that python.org offers, that is `py install 3.14`; the traditional 3.14 installer works too (keep its "py launcher" option). Python 3.11 to 3.13 also meet the requirements, but only 3.14 is tested; 3.15 cannot install the dependencies yet (`onnxruntime`, `pyclipper` and `PyYAML` have no Windows build for it).
- Microsoft Edge WebView2 Runtime (preinstalled on Windows 11) for the desktop window.
- For the game-data tools only: the Steam edition of Final Fantasy VII installed (they read its `scene.bin` and `kernel.bin`).

## Setup

```powershell
git clone https://github.com/SnipeAT/FFVII-Battle-Companion.git
cd FFVII-Battle-Companion
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

The dependencies are declared in `pyproject.toml`; the `dev` extra adds the test and packaging tools (httpx, PyInstaller). The editable install (`-e`) makes `import ffvii_companion` run the code in `src/` directly, so edits need no reinstall; `pip install -e .` installs the app without the development tools. The download is about 400 MB.

Keep the folder path short (under about 150 characters): the EXE is built into `dist\FFVII_Companion` inside the project, and some of its files sit deep in `_internal`.

## Running from source

```powershell
.\.venv\Scripts\python.exe -m ffvii_companion          # with a console showing the log
.\.venv\Scripts\pythonw.exe -m ffvii_companion         # without a console
```

The flags are in [Configuration](CONFIGURATION.md#command-line-flags). Runs from source keep their settings, profile, logs and reports in `.dev_home\` in the project (git-ignored), so they never touch the data of a packaged EXE.

`Launch_Desktop_App.bat` and `Launch_LAN_Mode.bat` prefer `dist\FFVII_Companion\FFVII_Companion.exe` when it exists: rebuild it after changing the source, or delete `dist` to run the source.

Without the game, the dashboard's **TEST HARNESS** button and the manual lookup field show enemy cards, and the simulator drives a running companion from the command line:

```powershell
.\.venv\Scripts\python.exe -m ffvii_companion.server.simulator tour    # cycle through preset encounters
.\.venv\Scripts\python.exe -m ffvii_companion.server.simulator         # trigger SOLDIER:3rd + Moth Slasher
.\.venv\Scripts\python.exe -m ffvii_companion.server.simulator clear   # clear the battle
```

## Project layout

The application is one Python package in `src/` (the "src layout"); tests, tools, build files and docs sit next to it and are not part of the app.

```
FFVII-Battle-Companion/
├── src/ffvii_companion/          # The application package (what the EXE contains)
│   ├── __main__.py               # python -m ffvii_companion; entry script of the EXE
│   ├── desktop.py                # Native desktop host (pywebview + embedded FastAPI server, PMv2 DPI, Efficiency mode)
│   ├── paths.py                  # Bundled resources vs. the writable data folder (EXE folder, .dev_home, tests)
│   ├── compendium.py             # Compendium: matching, formations, partners, variants, steal formula, battle summary, AI box data
│   ├── data/
│   │   ├── ai_scripts.json       # Enemy attacks and decompiled AI scripts
│   │   ├── enemies.json          # Enemy compendium
│   │   └── formations.json       # Encounter formations
│   ├── ppocr/                    # OCR models and their post-processing from RapidOCR (Apache-2.0: the .onnx files, postprocess.py)
│   ├── engine/
│   │   ├── capture_service.py    # Capture loop: text gate, OCR, battle state machine, adaptive sampling
│   │   ├── ocr_engine.py         # Lean PP-OCRv4 det + rec engine (ONNX Runtime) with recognition cache
│   │   ├── text_gate.py          # Sub-millisecond "did the on-screen text change?" check
│   │   ├── detector.py           # OCR line parsing, HUD detection, enemy matching
│   │   ├── capture.py            # mss screen capture and monitor selection
│   │   ├── window_finder.py      # Win32 FFVII window / process discovery
│   │   ├── calibration.py        # Settings, defaults, presets, validation, atomic persistence
│   │   ├── player_profile.py     # Party level, Sneak Glove, learned Enemy Skills
│   │   ├── detection_report.py   # Misdetection reports in the test-fixture format
│   │   ├── profiler.py           # Stage timings, sampling rate, OCR share, capture CPU, rotating log
│   │   └── win_qos.py            # Windows priority / EcoQoS helpers
│   └── server/
│       ├── main.py               # FastAPI app: REST API, /ws WebSocket hub, static dashboard
│       ├── models.py             # Pydantic models (Enemy, BattleState, steal calculator, AI box)
│       ├── simulator.py          # Preset encounters + CLI for exercising the dashboard
│       └── static/               # Dashboard (index.html, app.js, style.css)
├── tests/                        # unittest suite + recorded real-frame fixtures
├── tools/                        # Frame recorder, loop benchmark, synthetic frames, game-data tools
├── build_tools/                  # build_exe.py, package_release.py (release zip), ffvii_companion.spec (PyInstaller), licenses/
├── docs/                         # These documents and the README's screenshots
├── .github/workflows/tests.yml   # CI: the test suite on every push and pull request
├── pyproject.toml                # Package metadata and dependencies
├── Launch_Desktop_App.bat / Launch_LAN_Mode.bat                     # Double-click launchers
├── README.md / LICENSE / .gitignore                                 # User guide, MIT License, ignore rules
│
│   Not in the repository (git-ignored):
├── .venv/                        # Python environment (the package is installed here in editable mode)
├── .dev_home/                    # Data folder of runs from source: data/, logs/, reports/
├── recordings/                   # Recordings for tools/benchmark_loop.py
└── dist/FFVII_Companion/         # Packaged EXE, with its own data/, logs/, reports/; release zips next to it
```

`.gitignore` also lists the maintainer's local files (old code and AI agent instructions), which are not published.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

Individual suites:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_capture_service -v   # state machine on recorded frames
.\.venv\Scripts\python.exe -m unittest tests.test_ocr_engine -v        # OCR engine, cache, recorded OCR output
.\.venv\Scripts\python.exe -m unittest tests.test_text_gate -v         # text-change gate
.\.venv\Scripts\python.exe -m unittest tests.test_compendium -v        # matching, formations, steal formula
.\.venv\Scripts\python.exe -m unittest tests.test_data_integrity -v    # enemies.json / formations.json / ai_scripts.json checks
.\.venv\Scripts\python.exe -m unittest tests.test_server -v            # REST API and WebSocket
.\.venv\Scripts\python.exe -m unittest tests.test_calibration -v       # settings validation and persistence
.\.venv\Scripts\python.exe -m unittest tests.test_player_profile -v    # party level, Sneak Glove, learned Enemy Skills
.\.venv\Scripts\python.exe -m unittest tests.test_profiler -v
.\.venv\Scripts\python.exe -m unittest tests.test_detector -v
.\.venv\Scripts\python.exe -m unittest tests.test_platform -v          # screen capture, window finder, QoS, build and packaging helpers
.\.venv\Scripts\python.exe -m unittest tests.test_ai_script -v         # tools/ai_script.py disassembler, decompiler and name matching
```

The test package redirects all writable files (settings, player profile, reports, logs) to a temporary folder, so running the tests never changes your settings. GitHub Actions runs the whole suite on Windows for every push and pull request.

`tests/fixtures/real/` holds capture-region frames recorded from real FFNx sessions, battles and field menus; its `manifest.json` gives the expected result of each frame, and `ocr_expected.json` the OCR output of each frame. `tests.test_ocr_engine` compares the engine's output with that file, so run it after changing the OCR pipeline, ONNX Runtime, NumPy, OpenCV or Python; re-record the file (`python -m tests.test_ocr_engine --record`) only for a deliberate change of what the engine reads, and check the diff.

A misdetection report (**REPORT** in the dashboard) is saved in the same format: copy its frames and their `frames` entries from its `manifest.json` into `tests/fixtures/real/` to turn it into a regression test. Without the enemies the user typed in, the manifest records what was detected and `report.needs_review` is true.

## Tools

- `tools/record_frames.py` records the capture region of the running game (about one frame per second, `--interval` to change it), for tuning and benchmarks:
  ```powershell
  .\.venv\Scripts\python.exe -m tools.record_frames recordings\session1 --seconds 120
  ```
- `tools/benchmark_loop.py` replays a recording through the real capture loop and reports CPU time, OCR passes, the phase timeline and detection times. No recording ships with the project (they are large; `recordings/` is git-ignored): record one first, and pass `--fps-recorded` to match its `--interval` (2 for `--interval 0.5`). `--tree` benchmarks another copy of the project (for example an older commit exported with `git archive`) against the same recording, and `--json` writes the summary to a file:
  ```powershell
  .\.venv\Scripts\python.exe -m tools.benchmark_loop recordings\session1
  ```
  Results so far are in [Benchmarks](BENCHMARKS.md).
- `tools/ff7_synthetic.py` makes synthetic battle / field frames in the FFNx layout, used by the tests.

### Game data tools

Both read the Steam edition's `scene.bin` and `kernel.bin`.

- `tools/enrich_compendium.py` rewrites every game-data field of `enemies.json` and `formations.json`, keeping the curated ones (`id`, `name`, `notes`, `ai_summary`) and the order of the entries. Enemy Skills are taken from each enemy's attack list.
  ```powershell
  .\.venv\Scripts\python.exe -m tools.enrich_compendium              # update the data files
  .\.venv\Scripts\python.exe -m tools.enrich_compendium --check      # only report whether they are up to date
  .\.venv\Scripts\python.exe -m tools.enrich_compendium --variants   # list the enemies with several variants
  ```
- `tools/ai_script.py` prints an enemy's attacks (targets, power, status effects, elements) and its battle AI scripts, to check tactical notes against what the enemy really does. `--pseudo` decompiles the scripts into readable pseudo-code (if / else, switch, while, `use <attack>`) instead of listing the instructions; `--export` regenerates `data/ai_scripts.json` (`--check` only reports whether it is up to date), and `--candidates` lists the enemies whose AI deserves an `ai_summary`:
  ```powershell
  .\.venv\Scripts\python.exe -m tools.ai_script "Motor Ball"
  .\.venv\Scripts\python.exe -m tools.ai_script "Guard Scorpion" --pseudo
  .\.venv\Scripts\python.exe -m tools.ai_script --export
  ```

The `ai_summary` of an entry is null or a list of sections, `{"title": ..., "rows": [[when, does], ...]}`, with `"enemy_ids"` when a section applies to only some of the entry's `scene.bin` records (a boss fought several times). How the AI data is made: [How it works](HOW_IT_WORKS.md#enemy-ai).

## Building the EXE

```powershell
.\.venv\Scripts\python.exe build_tools\build_exe.py
```

PyInstaller runs outside the project (`%LOCALAPPDATA%\FFVIIBuild`, or the folder named by the `FFVII_COMPANION_BUILD_ROOT` environment variable, which the build deletes before and after and therefore refuses when it holds other files) so OneDrive never syncs the intermediate build. The build always uses the code of the project copy it belongs to, even with a virtual environment set up for another copy. Only when it succeeds are the files copied into `dist\FFVII_Companion\` in place: no folder is deleted or renamed, settings and logs next to the EXE are kept, and a failed build leaves the previous executable working. Close the companion before rebuilding; its files are locked while it runs. Output: `dist\FFVII_Companion\FFVII_Companion.exe`, with `FFVII_Companion.exe.config` next to it, which lets the .NET runtime behind the window load its files even when Windows marked them as downloaded.

The spec (`build_tools/ffvii_companion.spec`) collects every module of the package; a new data file of the package must be added to its `datas`.

Smart App Control treats an EXE you build like a downloaded one: where it blocks the EXE, run the companion from the Python environment instead (Python itself is code-signed).

## Packaging a release

```powershell
.\.venv\Scripts\python.exe build_tools\package_release.py
```

Builds the EXE as above, then writes `dist\FFVII_Companion-<version>-windows-x64.zip`: the build without the `data\`, `logs\` and `reports\` folders next to the EXE (your own settings, profile, logs and reports), plus `LICENSE.txt`, `README.md`, the `docs\` folder, `Launch_LAN_Mode.bat` and `THIRD_PARTY_LICENSES.txt` (the licenses of Python, the OCR models and the packages in the EXE). It refuses to run with uncommitted changes, so the zip always matches a commit; the zip's comment names it. The version comes from `__version__` in `src/ffvii_companion/__init__.py`.

## Git tips

The source is on GitHub: <https://github.com/SnipeAT/FFVII-Battle-Companion> (branch `main`); its history starts with the first public commit on 2026-10-11. `.gitignore` keeps the Python environment, builds, data folders of runs from source and recordings out of it.

If your copy is in OneDrive or another synced folder, keep the Git database outside it: syncing thousands of small object files can corrupt a repository. `git clone --separate-git-dir <folder outside OneDrive> https://github.com/SnipeAT/FFVII-Battle-Companion.git` puts the database in that folder and leaves a `.git` file in the project that points to it.

Useful commands:

```powershell
git log --oneline -15          # recent commits
git status --short             # uncommitted changes
git diff HEAD~1 --stat         # files changed by the last commit
git show <commit> -- <path>    # one file's change in a commit
```
