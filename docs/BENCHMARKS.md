# Benchmarks

How much CPU the companion uses while you play, and how fast it identifies enemies. Back to the [README](../README.md).

All measurements replay a recording of the capture region through the real capture loop with `tools/benchmark_loop.py` (see [Development](DEVELOPMENT.md#tools)), on an Intel Core i9-13900K, FFNx at 3840x2160, default settings. CPU is the process CPU time of every thread (including ONNX Runtime's), as a percentage of one core; the replays run without process-level Efficiency mode. How the companion keeps this low is described in [How it works](HOW_IT_WORKS.md#low-system-impact).

## Current version (2026-10-14)

A 300-second recording at 2 frames/s: 160 s in the field menus (items, materia, status, equipment), then a battle against Unknown 2 with its command menus in use (Enemy Skill, items).

| Avg CPU (% of one core) | Process CPU over 300 s | OCR passes | Screen grabs | Time in phase (idle / hunting / settled) | Unknown 2 detected |
| ---: | ---: | ---: | ---: | :--- | ---: |
| 12.4% | 37.2 s | 107 | 138 | 54% / 2.5% / 43% | 167.7 s |

The field menus keep changing their text, so the text-change gate passes most idle samples (one every 2 s) on to OCR; in the battle, one burst of hunting identifies Unknown 2 and the loop settles.

## Before and after the overhaul (2026-10-07)

A 150-second recording of two real battles (Prowler + Kalm Fang, then two Devil Rides) with the victory screen and field between them. Both versions ran with the same recording, capture region and sampling rates.

| Version | Process CPU over 150 s | Avg CPU (% of one core) | OCR passes | Battle 1 detected | "2x Devil Ride" detected |
| :--- | ---: | ---: | ---: | ---: | ---: |
| Before overhaul | 220.2 s | 146.8% | 201 | 2.19 s | 33.78 s |
| After overhaul | 7.3 s | 4.9% | 29 | 2.16 s | 33.19 s |

Same detections at the same moments with about 30x less CPU. Per OCR pass on these frames (single thread): RapidOCR call path 163 ms, new engine 127 ms, new engine with recognition cache 45 ms. The text-change gate sent 47 of the 150 frames to OCR and never skipped a frame whose text had changed.

## Settled phase and OCR tuning (2026-10-08)

A 153-second 4K video recording (no longer kept): field menus (0-45 s), a Devil Ride battle from 50 s with a summon animation from about 70 to 135 s, then the victory screen. The capture region was cropped from the video at 4 frames/s and replayed with `tools/benchmark_loop.py --fps-recorded 4`. The video was recorded with washed-out levels (white text at about 155 instead of 255), so the frames were stretched linearly to full range first; both versions ran on the same frames. Before: settled phase re-checked the screen every 0.35 s and woke early on any text change, `det_limit_side_len` 960, recognition batches of 6. After: no grabs between samples, 640, one crop per inference.

| Version | Avg CPU (% of one core) | OCR passes | Screen grabs | Summon 70-140 s: CPU / OCR / grabs | Devil Ride detected (name on screen 54.75-55.5 s) |
| :--- | ---: | ---: | ---: | ---: | ---: |
| Before | 44.2% | 128 | 242 | 60.2% / 50 / 125 | 55.22 s |
| After | 12.8% | 87 | 103 | 14.5% / 22 / 24 | 55.44 s |

The summon's bright effects kept waking the old settled phase (125 grabs in 70 s), so the phase meant to be the cheapest ran OCR faster than hunting. A cost of the change: the battle now clears up to `idle_timeout_seconds` after the victory screen (5.3 s here instead of 0.8 s).

The same recording also showed the field menus (status, item) read as the battle HUD in 78 of 196 frames, keeping the loop in the hunting phase (1.5 FPS, EcoQoS off) while browsing menus, and `auto_add_scene_partners` adding a Kalm Fang that was not there (Devil Ride can also appear alone). With both fixed: field menus stay idle (hunting share 26% -> 4% of the session, 65 OCR passes, 70 grabs), and the battle is detected as "Devil Ride" at 55.02 s. Average process CPU reads 15.9% instead of 12.8%, because more of the work now runs under EcoQoS: on efficiency cores the same OCR pass takes about 4x the CPU time (status menu frame 193 ms -> 800 ms, battle frame 125 ms -> 464 ms) but does not compete with the game's performance cores. Compare CPU numbers only between runs with the same phase mix.

## Recognition width: measured, not adopted (2026-10-16)

The recognition model gets every text box padded to at least 320 px wide (at a height of 48 px, as RapidOCR does), and most boxes are much shorter, so recognizing each box at its own width looked like an easy saving. `tools/ocr_diff.py` recorded all 2392 frames of four 300-second recordings (battles, field menus, 7507 recognized boxes) with each variant, against the current engine run at the same time under the same load:

| Variant | Recognition time | Frames whose detection outcome changed |
| :--- | ---: | ---: |
| Every box at its own width (multiple of 8) | -51% | 30 |
| Own width, at least 256 / 192 / 160 / 128 px | -18% / -32% / -38% / -44% | 1 / 2 / 11 / 31 |
| Own width plus an 8 or 16 px right margin | -49% / -47% | 33 / 40 |
| Boxes narrower than 64 / 96 / 128 px keep 320, longer ones their own width | -43% / -20% / -12% | 30 / 1 / 0 |

No variant changed an enemy read. Every changed outcome is the battle HUD: the two-letter "HP" label is read as "H" or not at all once its box loses the padding (and once "Magic" as "Magi"), so the HUD check fails on that frame. The short labels that need the padding are most of the boxes, so the only variant without a changed outcome saves 12% of recognition time, which bounds the saving in process CPU below that. Not worth a change to what the engine reads; the 320 px padding stays.

## Measuring it yourself

Record the capture region while you play, then replay it (pass `--fps-recorded` to match the recording's `--interval`: 2 for `--interval 0.5`):

```powershell
.\.venv\Scripts\python.exe -m tools.record_frames recordings\session1 --seconds 300 --interval 0.5
.\.venv\Scripts\python.exe -m tools.benchmark_loop recordings\session1 --fps-recorded 2
```

`--tree <folder>` replays the same recording through another copy of the project, to compare two versions.
