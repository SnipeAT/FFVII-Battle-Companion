# Configuration

How to start the companion, its command-line flags, the calibration dialog, every setting, and where it keeps its files. Back to the [README](../README.md).

## Launchers

| Where | What to start |
| :--- | :--- |
| Release download | `FFVII_Companion.exe` opens the companion window. `Launch_LAN_Mode.bat` next to it starts it in LAN mode (for phones, tablets and other PCs) and prints the address to open there. |
| Project folder (built or run from source) | `Launch_Desktop_App.bat` starts `dist\FFVII_Companion\FFVII_Companion.exe` when it exists, otherwise the app from the Python environment without a console. `Launch_LAN_Mode.bat` does the same in LAN mode. |
| Development | `.\.venv\Scripts\python.exe -m ffvii_companion` (with a console, for its log output). |

LAN mode binds to `0.0.0.0:59314`, so other devices on your network can open the dashboard: see [Phone, tablet or another PC](../README.md#phone-tablet-or-another-pc-lan-mode) for the steps and the security note.

In the project folder the launchers prefer `dist\FFVII_Companion\FFVII_Companion.exe`. After changing the source, rebuild it (`build_tools\build_exe.py`) or the launchers keep running the previous build.

## Command-line flags

`FFVII_Companion.exe` and `python -m ffvii_companion` accept these flags; the launchers pass on any flags given to them (`Launch_LAN_Mode.bat --on-top`). To use one with the EXE every time, add it to the **Target** of a shortcut to it.

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--host <IP>` | `127.0.0.1` | Interface to bind. `0.0.0.0` lets phones, tablets and other PCs on your LAN connect; one LAN address (`192.168.1.5`) or an IPv6 address (`::1`, `::`) works too. |
| `--port <PORT>` | `59314` | HTTP and WebSocket port. If occupied, the next free port of the 49 after it is used, and the window title shows it. |
| `--on-top` | off | Start pinned Always on Top (also toggled with the **PIN** button). |
| `--no-capture` | off | Do not start the capture loop on launch (start it with **CAPTURE**). |
| `--normal-priority` | off | Do not run the companion in Windows Efficiency mode. |
| `--width <PX>` / `--height <PX>` | `1320` / `860` | Initial window size. |
| `--min-width <PX>` / `--min-height <PX>` | `0` | Minimum window size (0 = unrestricted). |
| `--debug` | off | Enable WebView2 developer tools (F12). |

Only one companion runs at a time: started again while one is running on the same port, it exits without opening a second window.

## Calibration

Open **CALIBRATION** in the dashboard header:

- **Game window auto-detection** shows the window that was found (**RE-SCAN** looks again).
- **Target menu detection preset** selects the capture region:
  - `center_target_box` (default, recommended): rows 66%-84%, columns 20%-80%. Covers the targeted enemy name and the top rows of the battle menus.
  - `bottom_half_battle_ui`: bottom 45% of the window.
  - `full_game_window`: the whole window (highest CPU cost; troubleshooting only).
- **Diagnostic snapshot** captures the region once and shows the recognized lines, matched enemies and stage timings.
- **Performance profiler** shows grab / OCR / match timings, the measured sampling rate, how often OCR actually ran, and the capture thread's CPU usage.
- **Auto-Include Formation Partners** adds the formation partners that are certain (in every matching formation) automatically.

## Settings file (`data\calibration.json`)

The calibration dialog saves to `data\calibration.json` in the data folder (see below). Every setting:

| Key | Default | Meaning |
| :--- | :--- | :--- |
| `mode` | `auto_window` | `auto_window` captures relative to the game window; other values capture relative to the monitor. |
| `monitor_index` | `2` | Monitor captured when `mode` is not `auto_window` (mss numbering, 1-based). In `auto_window` mode the capture loop waits for the game window; only the diagnostic snapshot and REPORT capture this monitor when the window is not found. |
| `idle_fps` / `active_fps` / `settled_fps` | `0.5` / `1.5` / `0.35` | Sampling rate per phase (see [How it works](HOW_IT_WORKS.md#adaptive-sampling-three-phases)). |
| `idle_timeout_seconds` | `6.0` | Seconds without battle text before the battle is cleared. |
| `roi` | center target box | `top_pct`, `left_pct`, `width_pct`, `height_pct` of the capture region (fractions of the window). Every preset includes the battle window's HP and MP headers; keep them in a custom region too: outside a battle, the battle screen is recognized only with one of them (or by an enemy name). |
| `target_menu_preset` | `center_target_box` | Name of the active preset. |
| `confidence_threshold` | `0.45` | Minimum OCR recognition score for a text line. |
| `det_limit_side_len` | `640` | Longest side of the image fed to text detection (lower = less CPU). The capture region is first scaled to at most 1280 px wide, so this halves it. |
| `ocr_threads` | `1` | ONNX Runtime threads per OCR session. |
| `text_gate` | `true` | Skip OCR when the text in the region has not changed. |
| `eco_mode` | `true` | Run the capture thread on efficiency cores outside the hunting phase. |
| `auto_add_scene_partners` | `false` | Automatically add formation partners that are certain: enemies that every formation matching the enemies seen so far contains. Merely possible partners stay on the partners bar. |
| `dashboard_host_names` | `[]` | Extra names the dashboard's address may use, such as a name your router gives the PC (`["gaming-pc.lan"]` for `http://gaming-pc.lan:59314`). Only for the "Host name not allowed" page: IP addresses, `localhost` and the PC's own name (also with `.local`) always work. It lists names, not devices: it does not help when another device cannot reach the dashboard at all (see [Troubleshooting](../README.md#troubleshooting)). `["*"]` allows any name and turns off the protection against DNS rebinding (the log warns at every start): list the name instead. Only a hand edit of the file changes it (the API refuses it); restart the companion afterwards. Called `allowed_hosts` in versions 2.1.0 to 2.2.1; files that use the old name still work. See the security note under [Phone, tablet or another PC](../README.md#phone-tablet-or-another-pc-lan-mode). |

Values are validated: the API rejects invalid settings (HTTP 422), and out-of-range values in a hand-edited file are clamped on load. The defaults are the ones in this table (`DEFAULT_CONFIG` in `src/ffvii_companion/engine/calibration.py`); the file holds only what was saved from the dashboard or edited by hand, so a default that a later version changes reaches you unless you set that value yourself. Files saved by version 2.0.4 or earlier hold every value: delete a line to go back to its default. A save that fails (for example while another program holds the file) changes nothing, and the dashboard shows the old value again.

## Where files are saved

Settings, the player profile, misdetection reports and logs go to one data folder:

| Run | Data folder |
| :--- | :--- |
| Packaged EXE | Next to the executable: the folder a release was extracted to, or `dist\FFVII_Companion\` for a build of your own. Updating or rebuilding the EXE keeps it. |
| From source (desktop app, preview server, tools) | `.dev_home\` in the project (git-ignored). Nothing else is written into the project. |
| Tests | A temporary folder, deleted afterwards. |

| File | Contents |
| :--- | :--- |
| `data\calibration.json` | The settings above. |
| `data\profile.json` | The player profile: party level, Sneak Glove and the Enemy Skills you ticked. Shared by every dashboard, so the desktop window and other devices show the same odds and checklist. |
| `logs\companion.log` | The application log (rotating, 1 MB x 2). Windowed launches have no console, so errors end up here. |
| `logs\performance.log` | Periodic summary lines: sampling rate, OCR passes, capture CPU, stage timings. |
| `reports\<date-time>\` | Misdetection reports saved with **REPORT**. |

The `FFVII_COMPANION_HOME` environment variable overrides the folder. Folders and log files are created only when something is written: importing the code or starting the server creates nothing.
