# How it works

How the companion turns the game window into enemy cards: the capture pipeline, how often it looks at the screen, how it stays light on the PC, where its game data comes from, and how the steal odds and the enemy AI are worked out. Back to the [README](../README.md).

Module and data paths on this page are relative to the package folder `src/ffvii_companion/`. How the code itself is organized: [Architecture](ARCHITECTURE.md).

## The pipeline

```
 game window ROI ──> text-change gate ──> OCR (det + rec) ──> line parser ──> enemy matcher
   (mss grab)        skip if unchanged     PP-OCRv4 ONNX       stop words,      RapidFuzz over
                                           + recognition cache  instance letters  the compendium
                                                                                     │
 dashboard <── WebSocket /ws <── battle state machine <── formation deduction <──────┘
 (desktop / LAN)                 idle / hunting / settled    data/formations.json
```

1. **Capture.** `engine/window_finder.py` finds the FFVII window (title `FINAL FANTASY VII` or an `ff7*.exe` / `ffvii*.exe` process). `engine/capture_service.py` grabs only the calibrated region of interest (default: 60% of the width, rows 66%-84% of the height, where the targeted enemy name and the top of the battle menus appear).
2. **Text-change gate.** `engine/text_gate.py` reduces the region to a tiny "bright, low-saturation pixel" signature (FFVII's menu and target text is white) and compares it with the signature from the last OCR pass. If no text-like pixels changed, OCR is skipped and the last result is reused. The animated 3D scene, colored gauges and the blue windows do not trigger OCR.
3. **OCR.** `engine/ocr_engine.py` runs the PP-OCRv4 detection and recognition models (RapidOCR's ONNX conversion, bundled in `ppocr/`) in single-threaded ONNX Runtime sessions with thread spinning disabled. Recognition is cached per text box: boxes that did not move or change keep their text, and only new or changed boxes are recognized.
4. **Parsing and matching.** `engine/detector.py` drops HUD labels, commands, party names and overlay text, extracts instance letters (`Devil Ride B` means at least two Devil Rides; a letter only counts when some formation holds that many of the enemy, so a misread `Jenova·BlRTH` is not eight Jenovas), and fuzzy-matches the remaining candidates against `data/enemies.json`, trying each name both as read and without a trailing letter or digit (`Unkn0wn 2` is Unknown 2, not Unknown).
5. **Formation deduction.** `compendium.py` narrows the encounter using `data/formations.json`: it predicts formation partners, ignores a battle-menu item named like an enemy (Grenade) that cannot join the current enemies, and detects Battle Square / Arena round changes (any other new enemy that cannot share a formation with the current ones starts a new encounter).
6. **Broadcast.** `server/main.py` pushes battle updates over the `/ws` WebSocket to every connected dashboard (`server/static/`). Requests from other web origins are rejected for anything that changes state.

Everything runs on your PC: the OCR models are part of the program, and nothing is sent over the internet.

## Adaptive sampling (three phases)

| Phase | Default rate | When |
| :--- | :--- | :--- |
| **idle** | 0.5 FPS (`idle_fps`) | Field / world map; no battle HUD visible |
| **hunting** | 1.5 FPS (`active_fps`) | Battle HUD visible but no enemy registered yet, or a new enemy just appeared (burst of 3 frames) |
| **settled** | 0.35 FPS (`settled_fps`) | Enemies registered. Nothing is captured between samples; a new enemy name or instance letter is picked up at the next sample (within about 3 s) and switches back to hunting |

**Detection-time goal.** A new name is picked up within one sampling interval of the current phase plus one OCR pass: about 2 s for the first sample of a new battle (idle), about 0.7 s while hunting, and about 3 s for a newly targeted enemy once the battle is settled (default rates). Low CPU use comes first, so the slower idle and settled phases are the intended trade-off, not a latency gap; there is no single target for every phase.

The battle ends (back to **idle**) at the first sample taken `idle_timeout_seconds` (default 6 s) or more after a sample last showed enemies or the battle HUD, and never on a single sample without them; a game window that is closed or minimized counts the same. The time is measured, so a PC that reads slower than the sampling rate does not stretch it. The text-change gate applies in every phase, with a safety net that forces OCR after 2 skipped frames while hunting and after 4 skipped frames otherwise.

The rates and the timeout are settings: see [Configuration](CONFIGURATION.md#settings-file-datacalibrationjson).

## Low system impact

The companion is designed to run next to the game without competing with it:

- **Windows Efficiency mode** for the whole companion process: below-normal priority plus EcoQoS, so its threads run on efficiency cores and the WebView2 UI inherits the lower priority. The capture thread opts out of EcoQoS only while *hunting*, so new enemies are still identified at full speed. Disable with `--normal-priority`; the capture thread then keeps its own below-normal priority, and EcoQoS outside *hunting* as long as `eco_mode` is on.
- **OCR only when the text changes** (text-change gate) and **only for the boxes that changed** (recognition cache).
- **Single-threaded OCR without spin-waiting.** RapidOCR's default sessions kept idle worker threads busy-waiting after every frame, which cost more CPU than the OCR itself.
- **Detection at the configured size.** `det_limit_side_len` (640) is honored; the previous RapidOCR call path silently detected at up to 1500 px.
- **Recognition one text box at a time** instead of RapidOCR's batches of 6, which pad every box to the widest one.
- **No angle-classifier model** loaded (battle text is always horizontal).
- **Static dashboard.** No infinite CSS animations or backdrop blurs, so the WebView renders only when something changes.
- **No polling** from the dashboard; updates arrive over the WebSocket.

The measurements are in [Benchmarks](BENCHMARKS.md).

## Game data

- `data/enemies.json` - 295 enemies from the game's battle data (level, HP / MP, EXP / AP / Gil, combat stats, elemental affinities for all 16 elements including Cut, Hit, Punch, Shoot, Shout and Hidden, status immunities, every steal slot, drops, morph, Enemy Skills, Manipulate commands, encounter scenes, the name as the game writes it) plus curated tactical notes and AI summaries (`ai_summary`: when the enemy does what, in plain words).
- `data/ai_scripts.json` - each enemy's attacks (target, power, MP, elements, statuses) and battle AI scripts decompiled into readable pseudo-code, one record per distinct scene.bin record, with the actions it answers with an attack (`reacts`).
- `data/formations.json` - 961 encounter formations (enemy counts and scene.bin enemy ids per formation, whether the battle can be escaped, battle layout) used for partner prediction, round-change detection, variant selection and the battle summary. Enemy names match `enemies.json` exactly; one debug formation (scene 6, 6 x Adamantaimai) is marked `unused` and ignored.

Everything except the curated fields (`id`, `name`, `notes`, `ai_summary`) is generated from the game's `scene.bin` and `kernel.bin` by `tools/enrich_compendium.py` and `tools/ai_script.py` (see [Development](DEVELOPMENT.md#game-data-tools)).

**Variants.** Sixteen enemies have several different records under one name: bosses fought more than once (the Turks, Right / Left Arm, Ultimate Weapon, Bizarro·Sephiroth), Battle Arena tiers (CMD.Grand Horn, Mystery Ninja), the Chocobo of each area, and forms that share one battle (Mighty Grunt, Flower Prong, Lost Number, Hell House, Aero Combatant, Gargoyle). Such entries carry a `variants` list, and the dashboard shows the record of the current battle: the formation deduced from the enemies on screen says which record it is. When the enemies on screen fit several of them (Rude with Reno at Gongaga and at Midgar, Ultimate Weapon's flights), the card lists the candidates and shows the one closest to your party level. Formation totals add up each instance's own record.

## Steal odds

The steal calculator implements the game's formula (Final Fantasy Wiki, *Steal (Final Fantasy VII ability)*, after Terence Fergusson's Battle Mechanics FAQ):

```
LVdiff   = min(100, 40 + Your Level - Enemy Level)      (Sneak Glove: LVdiff = 100)
LVfactor = floor(512 * LVdiff / 100)
Chance   = floor(Item Steal Value * LVfactor / 256)
Success when Rnd(0..63) <= Chance, i.e. P = (Chance + 1) / 64
```

The item steal value comes from the enemy data (8 = rare, 32 = common, 63 = very common); each card shows the value, its odds at equal levels, and your odds at the party level of the player profile. Enemies with several steal items (Mighty Grunt, Malboro, Behemoth, Bagrisk) list all of them: the game tries the slots in order and stops at the first success, so a later item's odds are its own chance times the chance that every earlier slot fails.

## Enemy AI

Each enemy's behavior in FFVII is a set of small scripts in `scene.bin`: one for the start of the battle, one for each of its turns, counters to physical attacks, magic or any action, and one for its death. They are bytecode for a stack machine.

- **Decompiling.** `tools/ai_script.py` decodes the bytecode and rebuilds the structure the game's script compiler started from: if / else, switch with several values per case, while loops, assignments to the enemy's own variables and fields of units (`Self.HP`, `Targets.Death`), "use <attack>" and the battle text it shows. All 892 scripts in `scene.bin` decompile into structured code. The names of the opcodes and memory addresses follow Terence Fergusson's notes and the Qhimm wiki's Battle Script and Battle AI Addresses pages; values whose meaning is not known stay numbers in brackets.
- **The data file.** `--export` writes `data/ai_scripts.json`: for every compendium entry, the attacks and decompiled scripts of each distinct `scene.bin` record. Identical copies of an attack are listed once.
- **Counters and death actions.** The "can counter" and "can act on death" chips come from the counter and death scripts that can use an attack aimed at someone. An animation that targets no one, like a boss's death animation, does not count.
- **Summaries.** The behavior summaries are written by hand from the decompiled scripts: one for every boss, and for regular enemies whose AI is worth knowing (an Enemy Skill they use only under a condition or only through Manipulate, counters, self-destructs, special mechanics). The tests check that every "Learning <skill>" tip names a skill the enemy's script really uses on the party: a skill an enemy casts only on itself or its allies can be learned only through Manipulate.

The decompiled scripts show what the game decides, but some conditions are approximate in plain words: a script's own variables (`VarXXXX`) are counters and flags whose purpose is often only clear from how the script uses them.
