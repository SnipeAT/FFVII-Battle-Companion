# Changelog

What changed in each release, newest first. The downloads and the full release notes are on the [Releases page](https://github.com/SnipeAT/FFVII-Battle-Companion/releases). Versions follow `MAJOR.MINOR.PATCH`: a minor version adds features, a patch fixes things or corrects data.

## Unreleased

### Detection
- Switching away from the game during a battle (Alt+Tab out of fullscreen minimizes it) no longer clears the battle from the dashboard after about a second. The battle now stays for `idle_timeout_seconds` (6 s by default), as it does after a victory; it took up to 17 s while the companion was still identifying enemies.

### Dashboard
- An enemy added from the **Formation Partners** bar during a detected battle stays on the dashboard. The next change the companion read (a second enemy of the same kind, for example) removed its card again.
- Partners added automatically (`auto_add_scene_partners`) or from the partners bar show how many of them the battle holds when every possible formation agrees: Grangalan's battle shows three Grangalan Jr.Jr., not one.
- The steal calculator always shows the odds for the level in its box. Typing a two-digit level on a slow network could leave the odds of its first digit on screen.
- When a capture region preset cannot be saved, the calibration box keeps the saved preset highlighted instead of the one that was clicked.

### Settings and startup
- The companion starts on PCs that use a proxy server set in the Windows settings. It checked through that proxy whether its own server had started; a proxy on another machine cannot reach this PC's server, so the companion reported that it "did not start" and closed.

## 2.2.5 - 2026-10-23

### Dashboard
- The **PIN** button shows only in the desktop window. Phones, tablets and other PCs on the network showed it too, and tapping it pinned or unpinned the window on the PC.
- Ticking two Enemy Skills in quick succession in the checklist saves both; on a slow network the first tick could be lost.
- The partners bar no longer briefly offers an enemy that is already on screen, left over from the battle's previous state.
- The test snapshot, the steal calculator and the misdetection report say what went wrong when the companion cannot answer, instead of showing an unrelated script error, "undefined%" or the old result. A report that cannot be saved (for example on a full disk) leaves no half-written folder behind.

### Settings and startup
- A settings file edited by hand with an invalid capture mode (a list instead of a name) no longer makes the companion ignore every other saved setting; only the mode falls back to its default.
- When the port and the 49 after it are all in use, or `--port` is not a port number (1 to 65535), the companion says so instead of failing after 6 seconds with "did not start".

## 2.2.4 - 2026-10-16

### Performance
- The unpacked app is about 4 MB smaller: it no longer needs the Shapely library.

### Dashboard
- Closing "Full script" in an enemy's AI box no longer shrinks the box and squeezes its Attacks table into a narrow scrolling strip.
- The other boxes (calibration, steal calculator, Enemy Skills, misdetection report) open at their full width and centered; they were 10% narrower and sat slightly left of center.

## 2.2.3 - 2026-10-15

### Security
- A request with a very long enemy name, sent from a device on the network in LAN mode, could keep the companion busy for seconds. Names are now limited to 100 characters, and matching a name takes the same short time whatever it contains.
- When the companion's server fails to read or save a file, its reply no longer includes the error text, which could name folders on your PC; the details go to `logs\companion.log`.
- The dashboard escapes every value it puts on the page or into its requests.

## 2.2.2 - 2026-10-14

### LAN mode
- The setting `allowed_hosts` is now `dashboard_host_names`, because it lists names for the dashboard's address, not devices that may connect; settings files with the old name still work. Opening the dashboard under a name that is not allowed shows "Host name not allowed" with what to do, and the log warns while `"*"` turns the protection off.

### Documentation
- New [Architecture](docs/ARCHITECTURE.md) page: the codemap, threads and locks, the server's messages, where state lives, and the design decisions behind the unusual parts.
- New `CHANGELOG.md` (this file, also in the download), `CONTRIBUTING.md` and `SECURITY.md`.

## 2.2.1 - 2026-10-14

### LAN mode
- `Launch_LAN_Mode.bat` prints the address of the network connection Windows uses (the default route with the lowest metric) instead of possibly a virtual adapter's (WSL, Hyper-V, VirtualBox) that other devices cannot reach.
- Started with `--port`, the launcher prints that port instead of 59314.

The companion itself is unchanged.

## 2.2.0 - 2026-10-14

### Enemy AI
- An **AI** button on the enemy cards (293 of 295 enemies): what the enemy counters and whether it acts as it dies, its attacks with target, power, MP, element and statuses (Enemy Skills marked NEW or LEARNED), a plain-language behavior summary for every boss and 45 regular enemies, and the full script as readable pseudo-code.
- A boss fought several times shows the script of the current fight. The box loads only when opened.

### Corrections
- Dark Nation, Reno and Soul Fire notes corrected against their scripts.
- The odds of a second or later steal item read "Your odds per Steal" (numbers unchanged).

### LAN mode
- `Launch_LAN_iPad.bat` is now `Launch_LAN_Mode.bat`, for any browser on the network.

### Documentation
- A README written for players, with troubleshooting; the download includes a `docs` folder (configuration, how it works, development, benchmarks).

## 2.1.1 - 2026-10-12

Development tools only; the app is unchanged.
- `tools/ai_script.py` no longer prints junk lines after the last script of a scene, and finds names with a middle dot by the compendium's spelling.

## 2.1.0 - 2026-10-12

### Security
- The companion answers only requests addressed to an IP address, `localhost` or the PC's name (DNS rebinding protection). New setting `allowed_hosts` adds names, `["*"]` turns the check off; only a hand edit of the settings file changes it.

### Detection
- A misread letter no longer multiplies an enemy: an instance letter counts only when some battle can hold that many.
- Misread names ending in a number or letter find the right enemy ("Unkn0wn 2" is Unknown 2).
- Highlighting Grenade or Right Arm in the battle Item menu no longer replaces the battle.

### Settings and connections
- Settings and profile files saved as UTF-8 with BOM are read; a failed save changes nothing.
- `calibration.json` holds only the values you saved, so later releases can improve the defaults.
- A dashboard that missed an update reconnects; `--host` works with one LAN address and IPv6; a changed monitor applies without a restart; the window title shows the port the companion moved to; toggling CAPTURE during a slow step no longer leaves two loops running.

### Boss notes
- Jenova·SYNTHESIS and Motor Ball corrected.

## 2.0.4 - 2026-10-12

### Boss notes
- Proud Clod, Lost Number and Air Buster corrected.

## 2.0.3 - 2026-10-12

### Fixed
- The Steal Calculator no longer flickers when Sneak Glove is toggled.

### Boss notes
- Rapps corrected, and 18 boss notes corrected after a check against the decoded AI scripts and established guides.

## 2.0.2 - 2026-10-12

- Tactical notes for the story and optional bosses.
- The test suite runs on GitHub Actions (Windows, Python 3.14) for every push and pull request.

## 2.0.1 - 2026-10-11

- Built and tested with Python 3.14; the OCR models and their post-processing are bundled instead of the `rapidocr-onnxruntime` package.
- A name's last letter is no longer read as an instance letter.

## 2.0.0 - 2026-10-11

First public release.
