# Final Fantasy VII - Automated Real-Time Tactical Companion

A real-time combat companion for the PC release of Final Fantasy VII (1998 / 2012-2013 Steam edition and its re-release, with or without 7th Heaven / FFNx mods). It watches a small region of the game window, reads the battle text with on-device OCR, identifies the enemies you are fighting, and shows their full profile - stats and AP, elemental weaknesses and resistances, status immunities, steal items and your odds, drops and morphs, Enemy Skills and Manipulate commands - plus a summary of the whole encounter, on a second monitor, in a native desktop window, or on an iPad / tablet over your local network.

This is a fan project, not affiliated with or endorsed by Square Enix. It only reads what is on screen: it does not modify the game or read its memory. You need your own copy of Final Fantasy VII.

---

## Getting started

Windows 10 or 11 (64-bit) only. Pick one of the two ways below; both end with the same program.

### Option 1: download the ready-made EXE (no Python needed)

1. Open the [latest release](https://github.com/SnipeAT/FFVII-Battle-Companion/releases/latest) and download `FFVII_Companion-<version>-windows-x64.zip` from its **Assets**.
2. Right-click the downloaded zip, choose **Properties**, tick **Unblock** at the bottom if it is there, and click **OK**. Windows marks downloaded files as coming from the internet; unblocking the zip before extracting it keeps that mark off the extracted files, so Windows asks fewer questions about them.
3. Extract the zip to a folder you can write to, for example `Documents`. Not `Program Files`: the companion saves its settings and logs next to the EXE.
4. Double-click `FFVII_Companion.exe` in the extracted `FFVII_Companion` folder. If Windows SmartScreen says it protected your PC, click **More info**, then **Run anyway**: the EXE is not code-signed.
   - If Windows says **Smart App Control** blocked it (Windows 11 with Smart App Control on), there is no "Run anyway": Smart App Control blocks unsigned programs until Microsoft's cloud service has assessed them, which can take a while after a new release. Try again later, or run the companion from source instead (Option 2, which then needs no EXE).
5. Start the game and enter a battle: the companion finds the game window and reads the battle text by itself. See [Using it with 7th Heaven / FFNx](#using-it-with-7th-heaven--ffnx) for the dashboard, and [iPad, tablet or phone](#ipad-tablet-or-phone-local-wi-fi) to show it on another device (`Launch_LAN_iPad.bat` is in the same folder).

To update, delete `FFVII_Companion.exe` and the `_internal` folder, then extract the new release into the same place: the `data`, `logs` and `reports` folders (your settings, profile, logs and reports) stay. To uninstall, delete the folder.

### Option 2: build the EXE yourself from source

1. Install [Python 3.14](https://www.python.org/downloads/windows/) (64-bit). With the Python install manager that python.org offers, that is `py install 3.14`; the traditional 3.14 installer works too (keep its "py launcher" option). Python 3.11 to 3.13 also meet the requirements but are untested; 3.15 cannot install ONNX Runtime yet (no build for it).
2. Download **Source code (zip)** from the release's **Assets** and extract it (or clone the repository: `git clone https://github.com/SnipeAT/FFVII-Battle-Companion.git`).
3. Open PowerShell in the extracted folder (Windows 11: right-click inside the folder, **Open in Terminal**; Windows 10: Shift + right-click, **Open PowerShell window here**) and run the commands below. With another Python version, write it in the first one (for example `py -3.12`).
   ```powershell
   py -3.14 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
   .\.venv\Scripts\python.exe build_tools\build_exe.py
   ```
   The first command creates a private Python environment in `.venv`, the second downloads the dependencies (about 400 MB), the third builds the EXE (a few minutes).
4. Start `dist\FFVII_Companion\FFVII_Companion.exe`, or double-click `Launch_Desktop_App.bat`. Later rebuilds need only the third command, or a double-click on `Rebuild_EXE.bat`.

Smart App Control treats an EXE you build like a downloaded one. Where it blocks the EXE, run the companion from the Python environment instead (Python itself is code-signed): `.\.venv\Scripts\pythonw.exe -m ffvii_companion`. `Launch_Desktop_App.bat` does the same only when no EXE was built (it prefers `dist\FFVII_Companion\FFVII_Companion.exe`), so to use it, skip the third command or delete the `dist` folder.

---

## How it works

```
 game window ROI ──> text-change gate ──> OCR (det + rec) ──> line parser ──> enemy matcher
   (mss grab)        skip if unchanged     PP-OCRv4 ONNX       stop words,      RapidFuzz over
                                           + recognition cache  instance letters  the compendium
                                                                                     │
 dashboard <── WebSocket /ws <── battle state machine <── formation deduction <──────┘
 (desktop / iPad)                idle / hunting / settled    data/formations.json
```

Module and data paths in this README are relative to the package folder `src/ffvii_companion/` (see [Project layout](#project-layout)).

1. **Capture.** `engine/window_finder.py` finds the FFVII window (title `FINAL FANTASY VII` or an `ff7*.exe` / `ffvii*.exe` process). `engine/capture_service.py` grabs only the calibrated region of interest (default: 60% of the width, rows 66%-84% of the height, where the targeted enemy name and the top of the battle menus appear).
2. **Text-change gate.** `engine/text_gate.py` reduces the region to a tiny "bright, low-saturation pixel" signature (FFVII's menu and target text is white) and compares it with the signature from the last OCR pass. If no text-like pixels changed, OCR is skipped and the last result is reused. The animated 3D scene, colored gauges and the blue windows do not trigger OCR.
3. **OCR.** `engine/ocr_engine.py` runs the PP-OCRv4 detection and recognition models (RapidOCR's ONNX conversion, bundled in `ppocr/`) in single-threaded ONNX Runtime sessions with thread spinning disabled. Recognition is cached per text box: boxes that did not move or change keep their text, and only new or changed boxes are recognized.
4. **Parsing and matching.** `engine/detector.py` drops HUD labels, commands, party names and overlay text, extracts instance letters (`Devil Ride B` means at least two Devil Rides), and fuzzy-matches the remaining candidates against `data/enemies.json`.
5. **Formation deduction.** `compendium.py` narrows the encounter using `data/formations.json`: it prunes impossible enemies, predicts formation partners, and detects Battle Square / Arena round changes (a new enemy that cannot share any formation with the current ones starts a new encounter).
6. **Broadcast.** `server/main.py` pushes battle updates over the `/ws` WebSocket to every connected dashboard (`server/static/`). Requests from other web origins are rejected for anything that changes state.

### Adaptive sampling (three phases)

| Phase | Default rate | When |
| :--- | :--- | :--- |
| **idle** | 0.5 FPS (`idle_fps`) | Field / world map; no battle HUD visible |
| **hunting** | 1.5 FPS (`active_fps`) | Battle HUD visible but no enemy registered yet, or a new enemy just appeared (burst of 3 frames) |
| **settled** | 0.35 FPS (`settled_fps`) | Enemies registered. Nothing is captured between samples; a new enemy name or instance letter is picked up at the next sample (within about 3 s) and switches back to hunting |

**Detection-time goal.** A new name is picked up within one sampling interval of the current phase plus one OCR pass: about 2 s for the first sample of a new battle (idle), about 0.7 s while hunting, and about 3 s for a newly targeted enemy once the battle is settled (default rates). Low CPU use comes first, so the slower idle and settled phases are the intended trade-off, not a latency gap; there is no single target for every phase.

The battle ends (back to **idle**) when neither enemies nor the battle HUD have been seen for `idle_timeout_seconds` (default 6 s). The text-change gate applies in every phase, with a safety net that forces OCR after 2 skipped frames while hunting and after 4 skipped frames otherwise.

### Low system impact

The companion is designed to run next to the game without competing with it:

- **Windows Efficiency mode** for the whole companion process: below-normal priority plus EcoQoS, so its threads run on efficiency cores and the WebView2 UI inherits the lower priority. The capture thread opts out of EcoQoS only while *hunting*, so new enemies are still identified at full speed. Disable with `--normal-priority`.
- **OCR only when the text changes** (text-change gate) and **only for the boxes that changed** (recognition cache).
- **Single-threaded OCR without spin-waiting.** RapidOCR's default sessions kept idle worker threads busy-waiting after every frame, which cost more CPU than the OCR itself.
- **Detection at the configured size.** `det_limit_side_len` (640) is honored; the previous RapidOCR call path silently detected at up to 1500 px.
- **Recognition one text box at a time** instead of RapidOCR's batches of 6, which pad every box to the widest one.
- **No angle-classifier model** loaded (battle text is always horizontal).
- **Static dashboard.** No infinite CSS animations or backdrop blurs, so the WebView renders only when something changes.
- **No polling** from the dashboard; updates arrive over the WebSocket.

Measured on a recorded real battle session (150 frames from an FFNx 4K window, replayed through the real capture loop with `tools/benchmark_loop.py`), see [Benchmarks](#benchmarks).

---

## Requirements

- Windows 10 or 11 (64-bit)
- Python 3.14, only to run or build from source (the EXE contains its own Python). 3.11 to 3.13 also work but are untested; 3.15 cannot install `onnxruntime` yet (no build for it).
- Final Fantasy VII PC (Steam edition, re-release, or 1998), optionally with 7th Heaven / FFNx
- Microsoft Edge WebView2 Runtime (preinstalled on Windows 11) for the native desktop window

## Setup (from source)

```powershell
git clone https://github.com/SnipeAT/FFVII-Battle-Companion.git
cd FFVII-Battle-Companion
py -3.14 -m venv .venv                                  # Python 3.14 (see Requirements)
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"   # the app (editable) + tests and packaging (httpx, PyInstaller)
```

The dependencies are declared in `pyproject.toml`. The editable install (`-e`) makes `import ffvii_companion` run the code in `src/` directly, so edits need no reinstall; `pip install -e .` installs the app without the development tools.

## Launching

| Method | Command |
| :--- | :--- |
| Double-click launcher (recommended) | `Launch_Desktop_App.bat` - starts the packaged EXE if it exists, otherwise the app from source without a console |
| iPad / LAN mode | `Launch_LAN_iPad.bat` - binds to `0.0.0.0:59314` and prints the URL to open on the tablet |
| Packaged executable | `.\dist\FFVII_Companion\FFVII_Companion.exe` |
| Release download | `FFVII_Companion.exe` in the extracted folder; `Launch_LAN_iPad.bat` next to it starts LAN mode |
| Development | `.\.venv\Scripts\python.exe -m ffvii_companion` |

Note: the launchers prefer `dist\FFVII_Companion\FFVII_Companion.exe`. After changing the source, rebuild it (`Rebuild_EXE.bat`) or the launchers keep running the previous build.

### Command-line flags

`FFVII_Companion.exe` and `python -m ffvii_companion` accept:

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--host <IP>` | `127.0.0.1` | Interface to bind. `0.0.0.0` lets iPads / other devices on your LAN connect. |
| `--port <PORT>` | `59314` | HTTP and WebSocket port. If occupied, the next free port is used. |
| `--on-top` | off | Start pinned Always on Top (also toggled with the **PIN** button). |
| `--no-capture` | off | Do not start the capture loop on launch (start it with **CAPTURE**). |
| `--normal-priority` | off | Do not run the companion in Windows Efficiency mode. |
| `--width <PX>` / `--height <PX>` | `1320` / `860` | Initial window size. |
| `--min-width <PX>` / `--min-height <PX>` | `0` | Minimum window size (0 = unrestricted). |
| `--debug` | off | Enable WebView2 developer tools (F12). |

---

## Using it with 7th Heaven / FFNx

1. Start your modded game from 7th Heaven as usual (borderless or windowed both work; exclusive fullscreen may capture black frames).
2. Start the companion (`FFVII_Companion.exe` from a release, or `Launch_Desktop_App.bat` / `Launch_LAN_iPad.bat`). Move the window to your second monitor or pin it on top.
3. **CAPTURE: LIVE** glows green while the loop runs; the label shows the current phase.
4. In battle, open a command's target menu: the targeted name (`Custom Sweeper B`, `Devil Ride`) is recognized and its card appears. Instance letters raise the enemy count.
5. With one enemy identified, the **Formation Partners** bar lists enemies that can appear with it in that encounter; click one to add its card. Enable **Auto-Include Formation Partners** in Calibration to add the partners that are certain (in every matching formation) automatically.
6. The **battle summary** above the cards covers the whole encounter (see below).
7. Cards stay up during attack animations, summons and limit breaks, and clear after the victory screen.
8. If the companion shows the wrong enemy or count, press **REPORT** (see "Reporting a wrong detection").
9. **CLEAR** empties the battle on every dashboard and makes the capture loop forget the enemies it collected: the encounter starts over with the next name it reads, so a cleared enemy comes back only if it is read again.

### Battle summary

One strip above the cards, computed from every enemy on screen:

- **Formation total / seen so far**: EXP, AP and Gil. Once the formation is unambiguous (every formation that fits the enemies seen has the same members), the totals cover the whole formation, including enemies not targeted yet, and **Formation** lists its members.
- **Escape**: "Not possible" for boss and story formations, "Possible" otherwise; left out when the matching formations disagree.
- **Weak (all)**: elements every enemy is weak to. **Weak (some)**: elements some enemies are weak to that no enemy absorbs or ignores, safe to cast on all.
- **Avoid**: elements an enemy absorbs, is immune to or is fully healed by (for example "Fire (no effect on Bomb)").
- **Undead**: enemies that absorb Restorative, so healing magic and items damage them (cards show "[UNDEAD]").
- **Status (all)**: ailments no enemy in the battle is immune to.
- **E.Skill (new)**: Enemy Skills of these enemies that are not ticked in the checklist, highlighted; learned ones are listed without highlight.

### Enemy Skills and the player profile

- **E.SKILL** in the header opens the checklist of the 24 Enemy Skills with the enemies that have each one in their attack list; "(M)" means the skill is also in that enemy's Manipulate list. Tick the skills you have learned. Cards show each enemy's skills as NEW or LEARNED.
- The party level and Sneak Glove set in the steal calculator (**CALC ODDS**) are saved too: every card then shows your actual steal odds ("Your odds: 62.5% (party LVL 40)").
- These settings are the player profile, stored by the companion in `data\profile.json` of its data folder (see "Where files are saved"), so the desktop window and every tablet show the same odds and checklist.

### Reporting a wrong detection

**REPORT** in the header saves the capture region as it was at the last OCR pass (`last_ocr.png`) and right now (`current.png`), each with the recognized text and matched enemies, into `reports\<date-time>\` of the data folder. Optionally type the enemies that are really on screen ("Devil Ride x2, Kalm Fang") and a note. The `manifest.json` uses the format of `tests/fixtures/real/manifest.json`: copy the frames and their `frames` entries there to turn the misdetection into a regression test. Without expected enemies the manifest records what was detected and `report.needs_review` is true.

### Calibration

Open **CALIBRATION** in the header:

- **Game window auto-detection** shows the window that was found.
- **Target menu detection preset** selects the capture region:
  - `center_target_box` (default, recommended): rows 66%-84%, columns 20%-80%. Covers the targeted enemy name and the top rows of the battle menus.
  - `bottom_half_battle_ui`: bottom 45% of the window.
  - `full_game_window`: the whole window (highest CPU cost; troubleshooting only).
- **Diagnostic snapshot** captures the region once and shows the recognized lines, matched enemies and stage timings.
- **Performance profiler** shows grab / OCR / match timings, the measured sampling rate, how often OCR actually ran, and the capture thread's CPU usage.

### Configuration reference (`data\calibration.json`)

| Key | Default | Meaning |
| :--- | :--- | :--- |
| `mode` | `auto_window` | `auto_window` captures relative to the game window; other values capture relative to the monitor. |
| `monitor_index` | `2` | Monitor captured when `mode` is not `auto_window` (mss numbering, 1-based). In `auto_window` mode the capture loop waits for the game window; only the diagnostic snapshot and REPORT capture this monitor when the window is not found. |
| `idle_fps` / `active_fps` / `settled_fps` | `0.5` / `1.5` / `0.35` | Sampling rate per phase. |
| `idle_timeout_seconds` | `6.0` | Seconds without battle text before the battle is cleared. |
| `roi` | center target box | `top_pct`, `left_pct`, `width_pct`, `height_pct` of the capture region (fractions of the window). Every preset includes the battle window's HP and MP headers; keep them in a custom region too: outside a battle, the battle screen is recognized only with one of them (or by an enemy name). |
| `target_menu_preset` | `center_target_box` | Name of the active preset. |
| `confidence_threshold` | `0.45` | Minimum OCR recognition score for a text line. |
| `det_limit_side_len` | `640` | Longest side of the image fed to text detection (lower = less CPU). The capture region is first scaled to at most 1280 px wide, so this halves it. |
| `ocr_threads` | `1` | ONNX Runtime threads per OCR session. |
| `text_gate` | `true` | Skip OCR when the text in the region has not changed. |
| `eco_mode` | `true` | Run the capture thread on efficiency cores outside the hunting phase. |
| `auto_add_scene_partners` | `false` | Automatically add formation partners that are certain: enemies that every formation matching the enemies seen so far contains. Merely possible partners stay on the partners bar. |

Values are validated: the API rejects invalid settings (HTTP 422), and out-of-range values in a hand-edited file are clamped on load. The defaults are the ones in this table (`DEFAULT_CONFIG` in `engine/calibration.py`); the file in the data folder holds only what was saved from the dashboard or edited by hand.

### Where files are saved

Settings, the player profile, misdetection reports and logs go to one data folder, with the subfolders `data\`, `logs\` and `reports\`:

| Run | Data folder |
| :--- | :--- |
| Packaged EXE | Next to the executable: the folder a release was extracted to, or `dist\FFVII_Companion\` for a build of your own. Rebuilding the EXE keeps these subfolders. |
| From source (desktop app, preview server, tools) | `.dev_home\` in the project (git-ignored). Nothing else is written into the project. |
| Tests | A temporary folder, deleted afterwards. |

The `FFVII_COMPANION_HOME` environment variable overrides the folder. Folders and log files are created only when something is written: importing the code or starting the server creates nothing.

---

## iPad, tablet or phone (local Wi-Fi)

1. Launch in LAN mode: double-click `Launch_LAN_iPad.bat`, in the project folder or next to `FFVII_Companion.exe` in a release download (or start the app with `--host 0.0.0.0`). The script prints your PC's address.
2. Make sure the tablet is on the same network, then open `http://<YOUR_PC_IP>:59314` in Safari or Chrome. Find the address with `ipconfig` (IPv4 Address) if needed.
3. Optional: in Safari tap **Share > Add to Home Screen** for a full-screen, borderless app.
4. The first LAN launch may show a Windows Defender Firewall prompt: allow access on **Private networks**.

After an update of the companion, a dashboard left open on the tablet reloads itself when it reconnects, so it never runs the old version against the new server.

Security note: in LAN mode every device on your network can open the dashboard and use its controls (start/stop capture, calibration, diagnostic snapshots of the capture region). There is no login, so only use LAN mode on a network you trust. Web pages from other sites cannot use the companion: the server sends no cross-origin (CORS) headers, so they cannot read its responses, and it rejects state-changing requests and WebSocket connections whose `Origin` does not match the server.

---

## Data

- `data/enemies.json` - 295 enemies from the game's battle data (level, HP / MP, EXP / AP / Gil, combat stats, elemental affinities for all 16 elements including Cut, Hit, Punch, Shoot, Shout and Hidden, status immunities, every steal slot, drops, morph, Enemy Skills, Manipulate commands, encounter scenes, the name as the game writes it) plus curated tactical notes.
- `data/formations.json` - 961 encounter formations (enemy counts and scene.bin enemy ids per formation, whether the battle can be escaped, battle layout) used for partner prediction, round-change detection, variant selection and the battle summary. Enemy names match `enemies.json` exactly; one debug formation (scene 6, 6 x Adamantaimai) is marked `unused` and ignored.

`tools/enrich_compendium.py` reads the game's `scene.bin` and `kernel.bin` (Steam edition install) and rewrites every game-data field of the two files, keeping the curated ones (`id`, `name`, `notes`) and the order of the entries. Enemy Skills are taken from each enemy's attack list: it shows that the enemy has the skill, not when its AI uses it.

**Variants.** Sixteen enemies have several different records under one name: bosses fought more than once (the Turks, Right / Left Arm, Ultimate Weapon, Bizarro·Sephiroth), Battle Arena tiers (CMD.Grand Horn, Mystery Ninja), the Chocobo of each area, and forms that share one battle (Mighty Grunt, Flower Prong, Lost Number, Hell House, Aero Combatant, Gargoyle). Such entries carry a `variants` list, and the dashboard shows the record of the current battle: the formation deduced from the enemies on screen says which record it is. When the enemies on screen fit several of them (Rude with Reno at Gongaga and at Midgar, Ultimate Weapon's flights), the card lists the candidates and shows the one closest to your party level. Formation totals add up each instance's own record.

```powershell
.\.venv\Scripts\python.exe -m tools.enrich_compendium              # update the data files
.\.venv\Scripts\python.exe -m tools.enrich_compendium --check      # only report whether they are up to date
.\.venv\Scripts\python.exe -m tools.enrich_compendium --variants   # list the enemies with several variants
```

### Steal odds

The steal calculator implements the game's formula (Final Fantasy Wiki, *Steal (Final Fantasy VII ability)*, after Terence Fergusson's Battle Mechanics FAQ):

```
LVdiff   = min(100, 40 + Your Level - Enemy Level)      (Sneak Glove: LVdiff = 100)
LVfactor = floor(512 * LVdiff / 100)
Chance   = floor(Item Steal Value * LVfactor / 256)
Success when Rnd(0..63) <= Chance, i.e. P = (Chance + 1) / 64
```

The item steal value comes from the enemy data (8 = rare, 32 = common, 63 = very common); each card shows the value, its odds at equal levels, and your odds at the party level of the player profile. Enemies with several steal items (Mighty Grunt, Malboro, Behemoth, Bagrisk) list all of them: the game tries the slots in order and stops at the first success, so a later item's odds are its own chance times the chance that every earlier slot fails.

---

## Diagnostics and tools

- `logs\performance.log` (data folder) - periodic summary lines (sampling rate, OCR passes, capture CPU, stage timings).
- `logs\companion.log` (data folder) - application log (rotating, 1 MB x 2). Windowed launches have no console, so errors end up here.
- `tools/record_frames.py` - records the capture region of the running game (~1 frame per second) for tuning:
  ```powershell
  .\.venv\Scripts\python.exe -m tools.record_frames recordings\session1 --seconds 120
  ```
- `tools/benchmark_loop.py` - replays a recording through the real capture loop and reports CPU time, OCR passes, the phase timeline and detection times. No recording ships with the project (they are large; `recordings/` is git-ignored): record one with `tools/record_frames.py` first, and pass `--fps-recorded` to match its `--interval` (2 for `--interval 0.5`). `--tree` benchmarks another copy of the project (for example an older commit exported with `git archive`) against the same recording:
  ```powershell
  .\.venv\Scripts\python.exe -m tools.benchmark_loop recordings\session1
  ```
- `tools/ff7_synthetic.py` - synthetic battle / field frames in the FFNx layout, used by the tests.
- Simulator CLI (needs the app running):
  ```powershell
  .\.venv\Scripts\python.exe -m ffvii_companion.server.simulator tour    # cycle through preset encounters
  .\.venv\Scripts\python.exe -m ffvii_companion.server.simulator         # trigger SOLDIER:3rd + Moth Slasher
  .\.venv\Scripts\python.exe -m ffvii_companion.server.simulator clear   # clear the battle
  ```

## Benchmarks

`tools/benchmark_loop.py` on a 150-second recording of two real battles (Prowler + Kalm Fang, then two Devil Rides) with the victory screen and field between them, FFNx at 3840x2160, default settings, Intel Core i9-13900K. Process CPU covers every thread of the process (including ONNX Runtime workers). Both versions ran with the same recording, ROI and sampling rates, without process-level Efficiency mode.

| Version | Process CPU over 150 s | Avg CPU (% of one core) | OCR passes | Battle 1 detected | "2x Devil Ride" detected |
| :--- | ---: | ---: | ---: | ---: | ---: |
| Before overhaul | 220.2 s | 146.8% | 201 | 2.19 s | 33.78 s |
| After overhaul | 7.3 s | 4.9% | 29 | 2.16 s | 33.19 s |

Same detections at the same moments with about 30x less CPU. Per OCR pass on these frames (single thread): RapidOCR call path 163 ms, new engine 127 ms, new engine with recognition cache 45 ms. The text-change gate sent 47 of the 150 frames to OCR and never skipped a frame whose text had changed.

### Settled phase and OCR tuning (2026-10-08)

A 153-second 4K video recording (no longer kept): field menus (0-45 s), a Devil Ride battle from 50 s with a summon animation from about 70 to 135 s, then the victory screen. The capture region was cropped from the video at 4 frames/s and replayed with `tools/benchmark_loop.py --fps-recorded 4`. The video was recorded with washed-out levels (white text at about 155 instead of 255), so the frames were stretched linearly to full range first; both versions ran on the same frames. Before: settled phase re-checked the screen every 0.35 s and woke early on any text change, `det_limit_side_len` 960, recognition batches of 6. After: no grabs between samples, 640, one crop per inference.

| Version | Avg CPU (% of one core) | OCR passes | Screen grabs | Summon 70-140 s: CPU / OCR / grabs | Devil Ride detected (name on screen 54.75-55.5 s) |
| :--- | ---: | ---: | ---: | ---: | ---: |
| Before | 44.2% | 128 | 242 | 60.2% / 50 / 125 | 55.22 s |
| After | 12.8% | 87 | 103 | 14.5% / 22 / 24 | 55.44 s |

The summon's bright effects kept waking the old settled phase (125 grabs in 70 s), so the phase meant to be the cheapest ran OCR faster than hunting. A cost of the change: the battle now clears up to `idle_timeout_seconds` after the victory screen (5.3 s here instead of 0.8 s).

The same recording also showed the field menus (status, item) read as the battle HUD in 78 of 196 frames, keeping the loop in the hunting phase (1.5 FPS, EcoQoS off) while browsing menus, and `auto_add_scene_partners` adding a Kalm Fang that was not there (Devil Ride can also appear alone). With both fixed: field menus stay idle (hunting share 26% -> 4% of the session, 65 OCR passes, 70 grabs), and the battle is detected as "Devil Ride" at 55.02 s. Average process CPU reads 15.9% instead of 12.8%, because more of the work now runs under EcoQoS: on efficiency cores the same OCR pass takes about 4x the CPU time (status menu frame 193 ms -> 800 ms, battle frame 125 ms -> 464 ms) but does not compete with the game's performance cores. Compare CPU numbers only between runs with the same phase mix.

---

## Running the tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

Individual suites:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_capture_service -v   # state machine on recorded frames
.\.venv\Scripts\python.exe -m unittest tests.test_ocr_engine -v        # OCR engine, cache, recorded OCR output
.\.venv\Scripts\python.exe -m unittest tests.test_text_gate -v         # text-change gate
.\.venv\Scripts\python.exe -m unittest tests.test_compendium -v        # matching, formations, steal formula
.\.venv\Scripts\python.exe -m unittest tests.test_data_integrity -v    # enemies.json / formations.json checks
.\.venv\Scripts\python.exe -m unittest tests.test_server -v            # REST API and WebSocket
.\.venv\Scripts\python.exe -m unittest tests.test_calibration -v       # settings validation and persistence
.\.venv\Scripts\python.exe -m unittest tests.test_player_profile -v    # party level, Sneak Glove, learned Enemy Skills
.\.venv\Scripts\python.exe -m unittest tests.test_profiler -v
.\.venv\Scripts\python.exe -m unittest tests.test_detector -v
.\.venv\Scripts\python.exe -m unittest tests.test_platform -v          # screen capture, window finder, QoS
```

The test package redirects all writable files (settings, player profile, reports, logs) to a temporary folder, so running the tests never changes your settings. `tests/fixtures/real/` holds capture-region frames recorded from real FFNx sessions, battles and field menus (see its `manifest.json` for the expected result of each frame).

## Building the standalone executable

```powershell
.\.venv\Scripts\python.exe build_tools\build_exe.py
```

or double-click `Rebuild_EXE.bat`. PyInstaller runs outside the project (`%LOCALAPPDATA%\FFVIIBuild`, or the folder named by the `FFVII_COMPANION_BUILD_ROOT` environment variable, which the build deletes before and after and therefore refuses when it holds other files) so OneDrive never syncs the intermediate build. The build always uses the code of the project copy it belongs to, even with a virtual environment set up for another copy. Only when it succeeds are the files copied into `dist\FFVII_Companion\` in place: no folder is deleted or renamed, settings and logs next to the EXE are kept, and a failed build leaves the previous executable working. Close the companion before rebuilding; its files are locked while it runs. Output: `dist\FFVII_Companion\FFVII_Companion.exe`, with `FFVII_Companion.exe.config` next to it, which lets the .NET runtime behind the window load its files even when Windows marked them as downloaded.

### Packaging a release

```powershell
.\.venv\Scripts\python.exe build_tools\package_release.py
```

Builds the EXE as above, then writes `dist\FFVII_Companion-<version>-windows-x64.zip`: the build without the `data\`, `logs\` and `reports\` folders next to the EXE (your own settings, profile, logs and reports), plus `LICENSE.txt`, `README.md`, `Launch_LAN_iPad.bat` and `THIRD_PARTY_LICENSES.txt` (the licenses of Python, the OCR models and the packages in the EXE). It refuses to run with uncommitted changes, so the zip always matches a commit; the zip's comment names it. The version comes from `__version__` in `src/ffvii_companion/__init__.py`.

To publish it: tag the commit and push the tag (`git tag -a v<version> -m "Version <version>"`, then `git push origin v<version>`), then on GitHub open **Releases > Draft a new release**, choose the tag, attach the zip and click **Publish release**. GitHub adds the source code archives by itself.

## Version control

The source is on GitHub: <https://github.com/SnipeAT/FFVII-Battle-Companion> (branch `main`). Its history starts with the first public commit on 2026-10-11; the development before it (from 2026-10-07) was not published. `.gitignore` excludes `.venv`, `dist`, `.dev_home` (settings, logs and reports of runs from source), recordings, videos and the maintainer's local files: `archive/` (old code) and the instructions and review queue of the AI agents that develop and review the project (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `review/`, `.claude/`).

If your copy is in OneDrive or another synced folder, keep the Git database outside it, as the maintainer does: syncing thousands of small object files can corrupt a repository. `git clone --separate-git-dir <folder outside OneDrive> https://github.com/SnipeAT/FFVII-Battle-Companion.git` puts the database in that folder and leaves a `.git` file in the project that points to it.

Useful commands:

```powershell
git log --oneline -15          # recent commits
git status --short             # uncommitted changes
git diff HEAD~1 --stat         # files changed by the last commit
git show <commit> -- <path>    # one file's change in a commit
```

---

## Project layout

The application is one Python package in `src/` (the "src layout"); tests, tools, build files and docs sit next to it and are not part of the app.

```
FFVII-Battle-Companion/
├── src/ffvii_companion/          # The application package (what the EXE contains)
│   ├── __main__.py               # python -m ffvii_companion; entry script of the EXE
│   ├── desktop.py                # Native desktop host (pywebview + embedded FastAPI server, PMv2 DPI, Efficiency mode)
│   ├── paths.py                  # Bundled resources vs. the writable data folder (EXE folder, .dev_home, tests)
│   ├── compendium.py             # Compendium: matching, formations, partners, variants, steal formula, battle summary
│   ├── data/
│   │   ├── enemies.json          # Enemy compendium
│   │   └── formations.json       # Encounter formations
│   ├── ppocr/                    # OCR models and their post-processing, from RapidOCR (Apache-2.0, see LICENSE.txt there)
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
│       ├── models.py             # Pydantic models (Enemy, BattleState, steal calculator)
│       ├── simulator.py          # Preset encounters + CLI for exercising the dashboard
│       └── static/               # Dashboard (index.html, app.js, style.css)
├── tests/                        # unittest suite + recorded real-frame fixtures
├── tools/                        # Frame recorder, loop benchmark, synthetic frames, scene.bin enrichment
├── build_tools/                  # build_exe.py, package_release.py (release zip), ffvii_companion.spec (PyInstaller), licenses/
├── pyproject.toml                # Package metadata and dependencies
├── Launch_Desktop_App.bat / Launch_LAN_iPad.bat / Rebuild_EXE.bat   # Double-click launchers
├── README.md / LICENSE / .gitignore                                 # This file, MIT License, ignore rules
│
│   Not in the repository (git-ignored):
├── .venv/                        # Python environment (the package is installed here in editable mode)
├── .dev_home/                    # Data folder of runs from source: data/, logs/, reports/
├── recordings/                   # Recordings for tools/benchmark_loop.py
├── dist/FFVII_Companion/         # Packaged EXE, with its own data/, logs/, reports/; release zips next to it
└── archive/, CLAUDE.md, AGENTS.md, GEMINI.md, review/, .claude/   # The maintainer's local files
```

## License

The code and documentation are released under the MIT License (see `LICENSE`), except `src/ffvii_companion/ppocr/`: the OCR models (PaddleOCR's PP-OCRv4 in RapidOCR's ONNX conversion) and their post-processing code (adapted from RapidOCR) are under the Apache License 2.0, in `LICENSE.txt` in that folder. FINAL FANTASY VII is a registered trademark of Square Enix; the game's names, data and images remain theirs and are not covered by the MIT License. The enemy and formation data in `src/ffvii_companion/data/` was extracted from the game's files, and the frames in `tests/fixtures/real/` are screenshots of the game. The packaged EXE also contains Python and third-party packages under their own licenses: `THIRD_PARTY_LICENSES.txt` in the release zip lists them, with their license texts where the packages include them.