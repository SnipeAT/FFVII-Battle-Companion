# Architecture

How the code is organized, how its parts talk to each other, and the rules that hold across modules. Read this before changing the code; each module's own docstring has its details. Back to the [README](../README.md).

Module and data paths on this page are relative to the package folder `src/ffvii_companion/`; paths that start with `tests/`, `tools/` or `build_tools/` are relative to the project root. Names in `code` are searchable: they are the classes, functions and constants to look for.

## Bird's-eye view

The companion is one Windows process. `desktop.py` starts the FastAPI server on a thread, starts the capture loop on another, and opens the dashboard in a native WebView2 window on the main thread. Browsers on the LAN open the same dashboard from the same server.

```
 FFVII window ──grab──> capture loop ──callbacks──> server ──/ws──> dashboards
  (screen region)       engine/                     server/main.py   desktop window,
                        capture_service.py          battle_data()    browsers on the LAN
                          │  OCR, matching             │
                          └──> compendium.py <─────────┘
                               data/*.json (in memory)
```

The capture loop owns the battle as the screen shows it. The server owns what the dashboards see (`current_battle`), which manual lookups and the simulator can also set. Both read the compendium, an in-memory copy of the game data that never changes while the app runs.

## Codemap

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
├── tools/                        # Frame recorder, loop benchmark, OCR diff, synthetic frames, game-data tools
├── build_tools/                  # build_exe.py, package_release.py (release zip), ffvii_companion.spec (PyInstaller), licenses/
├── docs/                         # These documents and the README's screenshots
├── .github/workflows/tests.yml   # CI: the test suite on every push and pull request
├── .github/dependabot.yml        # Weekly update PRs for the workflow actions and Python dependencies
├── pyproject.toml                # Package metadata and dependencies
├── Launch_Desktop_App.bat / Launch_LAN_Mode.bat                     # Double-click launchers
├── README.md / CHANGELOG.md / CONTRIBUTING.md / SECURITY.md / LICENSE  # User guide, release history, contributing, security policy, MIT License
│
│   Not in the repository (git-ignored):
├── .venv/                        # Python environment (the package is installed here in editable mode)
├── .dev_home/                    # Data folder of runs from source: data/, logs/, reports/
├── recordings/                   # Recordings for tools/benchmark_loop.py
└── dist/FFVII_Companion/         # Packaged EXE, with its own data/, logs/, reports/; release zips next to it
```

`.gitignore` also lists the maintainer's local files (old code and AI agent instructions), which are not published.

The package has three layers, and imports only go downward:

- **Host:** `desktop.py` (and `__main__.py`). Parses the flags, sets DPI awareness and Efficiency mode, starts the server and the capture loop, owns the window.
- **Server:** `server/main.py` wires the capture loop's callbacks to WebSocket broadcasts and serves the REST API and the dashboard files. `server/models.py` holds the Pydantic models of the API, `server/simulator.py` the preset encounters.
- **Engine and data:** `engine/` (capture, OCR, detection, settings, profile, reports, profiling, Windows helpers) and `compendium.py` with `data/`. They know nothing of the server: the capture loop reports through callbacks.

## Runtime: threads, locks and singletons

| Thread | Started by | Runs | Priority |
| :--- | :--- | :--- | :--- |
| Main | Python | `webview.start()`: the window until it closes, then shutdown | Efficiency mode (process-wide: below normal, EcoQoS) |
| `UvicornServerThread` | `desktop.main()` | The asyncio event loop: REST requests, `/ws`, broadcasts | below normal, EcoQoS |
| `FFVIICaptureLoop` | `CaptureService.start()` | `CaptureService._loop()`: one sampling step, then sleep at the phase's rate | below normal; EcoQoS (`eco_mode`) except while hunting |
| Worker threads | `asyncio.to_thread` | `CaptureService.clear_battle()` for the dashboard's CLEAR, which can wait for a sampling step | process default |

Locks:

- `CaptureService._capture_lock` serializes everything that uses the screen grabber, the OCR session and the text gate, which are not thread-safe: a sampling step (grab plus `process_frame()`), `capture_snapshot()`, `save_detection_report()` and `clear_battle()`.
- `CaptureService._state_lock` guards the battle state fields (`current_enemies`, `combat_phase`, `current_fps`, ...), which the server reads from its thread.
- `server/main.py`'s `battle_lock` guards `current_battle`, the state the dashboards see.
- `EnemyCompendium._match_lock` guards the cache of `match_enemy()`, which the capture thread and request threads (search, simulations, reports) share.
- The callbacks fire outside `_state_lock` but inside `_capture_lock`, which a sampling step and `clear_battle()` both hold, so a CLEAR never interleaves with a step's broadcast. They run on the capture thread (or the worker thread of a CLEAR), so they hand messages to the event loop with `ConnectionManager.threadsafe_broadcast()` (`asyncio.run_coroutine_threadsafe`) instead of sending themselves.

Module-level singletons, created on import: `capture_service`, `compendium`, `calibration_manager`, `player_profile` and `perf_tracker`. Importing them reads files but writes nothing; folders and log files are created on the first write, so tools and tests can import the server freely.

## One sampling step

`CaptureService.process_frame()` is one step, and the tests drive the state machine through it directly:

1. **Text-change gate** (`TextChangeGate`): compare the region's signature with the one of the last OCR pass. Unchanged text reuses the last lines; `MAX_GATED_SKIPS` forces OCR after a few skipped frames anyway.
2. **OCR** (`BattleDetector.detect_text_lines()` -> `OCREngine.run()`): detection, then recognition of new or changed boxes only.
3. **Matching** (`BattleDetector.identify_enemies_from_lines()`, `is_combat_hud_active()`), memoized on identical lines.
4. **Battle state** (`_update_battle_state()`): add enemies and counts, deduce the formation (`EnemyCompendium.deduce_formation()`), detect Battle Square rounds, move between the idle, hunting and settled phases.
5. **Callbacks**: `on_battle_detected(enemies, title)` when the encounter changed, `on_battle_cleared()` when it ended, `on_phase_changed()` for a phase change without an encounter change.

What each phase means, its rate and the detection-time goal: [How it works](HOW_IT_WORKS.md#adaptive-sampling-three-phases).

## Server: messages and API

Every battle message goes through `battle_data()`: it applies each enemy's scene.bin variant for this battle (`EnemyCompendium.resolve_variants()`, on copies), adds the steal odds at the profile's party level and the battle summary. Compendium entries are never modified.

The `/ws` WebSocket, server to client:

| Message | When |
| :--- | :--- |
| `DASHBOARD_VERSION` | First on connect. A hash of the dashboard files; a tab that saw another version reloads (after an update) |
| `BATTLE_UPDATE` | On connect, then on every encounter change, clear, manual lookup, added partner, simulated battle and profile change |
| `PROFILE_UPDATE` | On connect, then after `POST /api/profile` (followed by a `BATTLE_UPDATE` with the new odds) |
| `CAPTURE_STATUS` | On connect, then when capture starts or stops and on a phase change without an encounter change |

Client to server, as `{"action": ...}`: `MANUAL_SELECT` and `ADD_ENEMY` (with `enemy_id`), and `CLEAR_BATTLE`, which clears the capture loop's encounter too, so the next name read starts a new one.

The REST API, by prefix: `/api/search`, `/api/enemies/{id}/partners` and `/api/enemies/{id}/ai` (the AI box, fetched when it opens), `/api/steal-calc`, `/api/profile`, `/api/enemy-skills`, `/api/report/misdetection`, `/api/simulate/*`, `/api/capture/*`, `/api/calibration/*`, `/api/performance*`, `/api/desktop/*` and `/api/health`. A running companion lists them all at `/openapi.json`.

The dashboard (`server/static/`) is plain HTML, JavaScript and CSS without a build step. It is event-driven: it renders what the WebSocket sends and fetches only on user actions.

## State and files

| What | Where | Written by |
| :--- | :--- | :--- |
| Code, `data/*.json`, `server/static/`, `ppocr/` | `PACKAGE_DIR`: next to the code; `<_MEIPASS>/ffvii_companion/` in the EXE | Nobody at run time (read-only) |
| Settings | `data/calibration.json` in the data folder | `CalibrationManager` |
| Player profile | `data/profile.json` in the data folder | `PlayerProfile` |
| Misdetection reports | `reports/<timestamp>/` in the data folder | `detection_report.write_report()` |
| Logs | `logs/companion.log`, `logs/performance.log` in the data folder | `desktop.py`, `PerformanceTracker` |

The data folder (`paths.get_user_data_dir()`) is the EXE's folder when packaged and `.dev_home/` in the project from source; the `FFVII_COMPANION_HOME` environment variable overrides both, and the test package sets it to a temporary folder. Where users find these files: [Configuration](CONFIGURATION.md#where-files-are-saved).

**Settings.** `DEFAULT_CONFIG` in `engine/calibration.py` holds every default, and nothing else does: no settings file is bundled. `get_config()` returns the defaults with the saved values over them, so code reads `cfg["key"]` without fallback values. The file holds only the values the user saved, so a default a later version changes reaches everyone who never set it. `validate_settings()` checks every change, a save is written (atomically) before it is applied, so a failed save changes nothing, and `dashboard_host_names` can only be changed in the file. `RENAMED_KEYS` maps old setting names to new ones when a file is loaded. The player profile follows the same pattern (`validate_profile()`).

## Game data

`data/enemies.json` (one entry per enemy name), `data/formations.json` (encounters) and `data/ai_scripts.json` (attacks and decompiled AI per entry) are loaded once by `EnemyCompendium`. Everything in them except the curated fields `id`, `name`, `notes` and `ai_summary` is generated from the game's `scene.bin` and `kernel.bin` by `tools/enrich_compendium.py` and `tools/ai_script.py --export`, which have `--check` modes; `tests/test_data_integrity.py` checks the files' structure and cross-references.

- `name` is the matching key (formations name enemies by it); `display_name` is the game's spelling, with its middle dot (`Jenova·LIFE`). Data files are UTF-8.
- An enemy whose scene.bin records differ (a boss fought several times) has a `variants` list; `resolve_variants()` picks the record from the formations that match the enemies on screen.
- `ai_summary` is popped out of the entries at load and served only by `get_ai()`, so battle updates never carry it; entries with AI scripts get `has_ai`.
- `tools/enrich_compendium.py` rewrites the generated fields but keeps the curated ones and the order of the entries; it adds and removes no enemy.
- An enemy that "absorbs" Restorative is stored with `healing_damages`, not with an absorbed element: healing hurts it.

What the data holds and how the AI is decoded: [How it works](HOW_IT_WORKS.md#game-data). The tools: [Development](DEVELOPMENT.md#game-data-tools).

## Security boundary

The server listens on `127.0.0.1` unless LAN mode binds it to `0.0.0.0`; LAN mode has no login and is for trusted networks. `SameOriginGuard` (in `server/main.py`) is the only gate:

- Every request and WebSocket addressed to a host name other than an IP address, `localhost`, the PC's name or a name in `dashboard_host_names` is rejected, so a DNS-rebinding page cannot reach the server. The 403 page says how to open the dashboard instead; `"*"` turns the check off, and the server logs a warning at every start while it does.
- State-changing requests (anything but GET, HEAD, OPTIONS) and WebSockets whose `Origin` does not match their `Host` are rejected. Requests without an `Origin` (the simulator, scripts, tests) pass.
- There are no CORS headers: the dashboard is always served by the same server, so other origins cannot read responses.

GET endpoints therefore must not change state. Reporting a vulnerability: [SECURITY.md](https://github.com/SnipeAT/FFVII-Battle-Companion/blob/main/SECURITY.md).

## Build and packaging

`build_tools/ffvii_companion.spec` collects every module of the package; a new data file of the package must be added to its `datas`. `build_tools/build_exe.py` builds outside the project and copies the result into `dist\FFVII_Companion\` in place, and `build_tools/package_release.py` zips it with the license, the README, `CHANGELOG.md`, `docs/`, the LAN launcher and the third-party notices it reads from the build. Commands and details: [Development](DEVELOPMENT.md#building-the-exe).

## Design decisions

Choices that look unusual but are deliberate. Change one only with a measurement or a failure the reasoning did not account for.

- **Low CPU before low latency.** Low sampling rates, OCR only when the text gate sees a change, and nothing grabbed between samples. An early-wake check between settled samples was removed: bright spell and summon animations kept waking it, so the settled phase ran OCR more often than hunting. The OCR tuning (one thread without spin-waiting, detection at 640 px, one recognition crop per inference, no angle classifier, the recognition cache) was measured on real frames: see [How it works](HOW_IT_WORKS.md#low-system-impact) and [Benchmarks](BENCHMARKS.md).
- **The compendium is JSON in memory, not a database.** 295 enemies, 961 formations and the AI scripts load in well under a second, lookups are dictionary and RapidFuzz searches, and nothing is written while the app runs.
- **The OCR models and their post-processing are bundled** in `ppocr/` (from `rapidocr-onnxruntime` 1.4.4, Apache-2.0) instead of depending on that package, whose last release installs only on Python 3.12 and older. `tests/test_ocr_engine.py` compares the engine's output on every fixture frame with output recorded from RapidOCR's own code.
- **One entry per enemy name, with variants.** The name is what the OCR reads and what formations use, so same-name records stay one entry; when several records still fit the battle, the card lists the candidates and shows the one closest to the party level.
- **Enemy Skills come from each enemy's attack list**, not its AI script: the list says the enemy has the skill, the AI box shows when it uses it. "(M)" means the skill is also in its Manipulate list.
- **The AI scripts ship as generated data.** They are decoded offline from the developer's copy of the game; the app never reads the player's game files.
- **The battle summary admits uncertainty.** `cannot_escape` is unknown when the matching formations disagree, and totals count only the enemies seen until every matching formation has the same members.
- **A static, event-driven dashboard.** No polling, continuous animations or backdrop blurs, so the WebView renders only when something changes. Its files are sent with `Cache-Control: no-cache` (`DashboardFiles`), and `DASHBOARD_VERSION` reloads tabs left open across an update.
- **Windows only.** Win32 calls go through `ctypes` and are guarded, so the modules still import elsewhere.
- **Builds happen outside the project folder.** A synced folder such as OneDrive can delete or lock freshly built files, so PyInstaller runs elsewhere and the result is copied into `dist` in place. The spec leaves out pywebview's PyInstaller hook package (PyInstaller's GPL code) and OpenCV's FFmpeg plugin (unused LGPL video code), and `build_exe.py` writes `FFVII_Companion.exe.config`, without which .NET refuses pywebview's DLL when a downloaded zip was extracted without unblocking.
- **The packaged app needs a short folder path** (under about 150 characters): its deepest file is about 105 characters long, and Windows' 260-character limit then refuses to load it.
- **Source layout with an editable install.** `pip install -e ".[dev]"` makes `import ffvii_companion` run the code in `src/`; tests, tools and launchers rely on it.

## Tests

The suite is plain `unittest` and needs no game: `tests/fixtures/real/` holds frames recorded from real sessions with their expected results (`manifest.json`) and OCR output (`ocr_expected.json`), `tools/ff7_synthetic.py` makes synthetic frames, and the capture tests feed frames to `process_frame()` with a fake grabber. A misdetection report is saved in the fixture format, so it can become a regression test. Commands: [Development](DEVELOPMENT.md#tests).
