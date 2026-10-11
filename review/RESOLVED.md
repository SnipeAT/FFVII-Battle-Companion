# Resolved Findings

Closed findings and the history of review sessions. Maintained by Claude. Reviewers read this file
before filing (closed items are not reported again without new evidence) and never edit it.
Verdicts are defined in `review/PROTOCOL.md` section 3.

## Review sessions

Baseline: Claude completed a full overhaul of the project on 2026-10-07. The pre-overhaul source and
the full diff are in `archive/` on the user's computer, which is not published.

Git: the project became a Git repository on 2026-10-08 (the sessions of 2026-10-07 have no commit)
and was published on GitHub on 2026-10-11 with a new history. The public history starts with one
commit (`git log --max-parents=0 --oneline`) that holds the project as it was after the triage of
GEMINI 2026-10-09 23:30, without `archive/`. The commits cited in this file belong to the private
development history before it, which was not published: `git show` cannot find them. The next
session uses the first public commit as the last reviewed commit. The changes since the last
session's commit (`e5f3bca`) are the fixes of GEMINI-20261009-01 to -03, described in their
resolutions below, and the publication (docs, `LICENSE`): check those fixes in the code and tests.

| Session | Reviewer | Commit | Scope | Tests | Findings | Outcome |
| :--- | :--- | :--- | :--- | :--- | ---: | :--- |
| GEMINI 2026-10-07 17:38 | Gemini 3.8 Flash, Antigravity | - | full review of the overhaul | 104 passed | 7 | 5 FIXED, 2 REJECTED |
| GEMINI 2026-10-07 20:05 | Gemini 3.8 Flash, Antigravity | - | features since last session (profile, Enemy Skills, battle summary, reports, enrichment) | 143 passed | 4 | 4 FIXED |
| GEMINI 2026-10-08 12:28 | Gemini 3.8 Flash, Antigravity | d584408 | changes since 2026-10-07 20:05 (settled phase, OCR tuning, field menu filtering, certain partners) and checklist areas 1, 2, 3, 5, 7, 8 | 153 passed | 4 | 4 FIXED |
| GEMINI 2026-10-08 16:20 | Gemini 3.8 Flash, Antigravity | ad330af | changes since d584408 (enemy variants, multi-slot steal odds, undead mechanics) and checklist areas 1, 3, 4, 5, 6, 7, 8, 9 | 177 passed | 1 | 1 FIXED |
| GEMINI 2026-10-08 17:31 | Gemini 3.1 Pro, Antigravity | 637cd0b | changes since ea436aa (dashboard versioning, vestigial code removal, .dev_home paths, src/ package move) | 182 passed | 0 | - |
| GEMINI 2026-10-08 17:36 | Gemini 3.8 Flash, Antigravity | 637cd0b | checklist areas 1, 2, 3, 6, 9, 10 on the src/ layout and packaging files | 182 passed | 0 | - |
| GEMINI 2026-10-08 19:46 | Gemini 3.8 Flash, Antigravity | 44e6f67 | changes since 0bfba2d (read-only folder removal, capture status broadcast) and checklist areas 1, 2, 3, 5, 7, 9 | 184 passed | 0 | - |
| GEMINI 2026-10-09 23:30 | Gemini 3.8 Flash, Antigravity | e5f3bca | full codebase, all checklist areas | 192 passed | 3 | 3 FIXED |

## Closed findings

| ID | Severity | Location | Title | Verdict | Resolution | Closed |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| GEMINI-20261007-01 | High | `data/database.py` | Unknown 2/3 collide with Unknown | FIXED | Canonical names indexed without suffix stripping, exact lookup first; tests added | 2026-10-07 |
| GEMINI-20261007-02 | High | `data/database.py` | Instance-letter regex truncates 102 names | REJECTED | Index keys and queries were truncated symmetrically, so every name still resolved; only real collision was Unknown (see -01). Indexing no longer truncates; test asserts every canonical name resolves to itself | 2026-10-07 |
| GEMINI-20261007-03 | Medium | `data/database.py` | Five aliases silently dropped | FIXED | Alias targets corrected, test added | 2026-10-07 |
| GEMINI-20261007-04 | Medium | `engine/capture_service.py` | Negative monitor coordinates clamped | FIXED | max(0) removed here and in tools/record_frames.py; test added | 2026-10-07 |
| GEMINI-20261007-05 | Medium | `desktop_app.py` | is_port_in_use fails on 0.0.0.0 | FIXED | Wildcard hosts probed via 127.0.0.1; test added | 2026-10-07 |
| GEMINI-20261007-06 | Medium | `desktop_app.py` | Double launch skips port reallocation | FIXED | A second instance now exits when a companion already answers on the port | 2026-10-07 |
| GEMINI-20261007-07 | Low | `engine/capture_service.py` | process_frame runs inside _capture_lock | REJECTED | Intentional: the lock serializes the shared detector, OCR session and text gate against capture_snapshot, which is not thread-safe | 2026-10-07 |
| GEMINI-20261007-08 | Medium | `data/enemies.json` | Enemy id `ying/yang` breaks /api/enemies routes (404) | FIXED | Id changed to `ying-yang`; tests assert no id contains path-breaking characters and every id is reachable via the API | 2026-10-07 |
| GEMINI-20261007-09 | Medium | `data/database.py` | `Unknown 2 A` resolves to Unknown | FIXED | `_normalize_text` keeps the digit when an instance letter was removed (same rule as the detector); test added | 2026-10-07 |
| GEMINI-20261007-10 | Medium | `build_exe.py` | install_bundle purges reports/ on rebuild | FIXED | `reports` added to PRESERVED_DIRS; install_bundle test added | 2026-10-07 |
| GEMINI-20261007-11 | Low | `engine/player_profile.py` | validate_profile clamp mode raises on unhashable skill entries | FIXED | Non-string entries ignored before building the set; test added | 2026-10-07 |
| GEMINI-20261008-01 | High | `engine/capture_service.py` | Hunting phase never clears when the game window closes before an enemy is registered | FIXED | `_note_window_missing()` counts when an enemy is registered or the phase is not idle, like `process_frame`; test added. Commit dedd7d9 | 2026-10-08 |
| GEMINI-20261008-02 | High | `server/main.py` | Cross-origin GET on /api/calibration/snapshot bypasses SameOriginGuard and burns CPU | FIXED | Endpoint is POST-only (the guard checks POST), dashboard updated; test covers GET 405, cross-origin POST 403. Commit 75f56ff | 2026-10-08 |
| GEMINI-20261008-03 | Medium | `engine/capture_service.py` | Accumulated enemy keeps a stale instance letter when the count rises | FIXED | The letter follows the count; test added. Commit dedd7d9 | 2026-10-08 |
| GEMINI-20261008-04 | Medium | `engine/capture_service.py` | capture_snapshot uses stale detector settings while capture is stopped | FIXED | `capture_snapshot` syncs the detector from the current calibration inside the capture lock; test added. Commit dedd7d9 | 2026-10-08 |
| GEMINI-20261008-05 | Medium | `server/static/app.js` | Card keeps stale variant candidates when a partner narrows them without changing the variant shown | FIXED | `variantKey` includes the candidate indices, so the card rebuilds. No JS test harness: verified in the dashboard (Rude then Reno at party level 15, Chocobo then Levrikon). Commit 7bece1d | 2026-10-08 |
| GEMINI-20261009-01 | Medium | `engine/capture_service.py` | Instance letter A ignored when enemy was first registered without instance letter | FIXED | At the same count, a lettered read replaces an unlettered one (capture loop and the detector's same-frame merge); tests added. Real impact Low: no dashboard element shows the letter. The auto-added partner case was not affected (partner entries have no count). Commit 2e9bf80 | 2026-10-10 |
| GEMINI-20261009-02 | Medium | `compendium.py` | get_partners ignores queried enemy_id when current_observed is provided | FIXED | `get_partners` narrows by the battle only when it contains the enemy; the endpoint also merges the capture loop's counts only into a live (ocr) battle, which emptied the partners bar during a manual or simulated one; tests added. Commit 1295c23 | 2026-10-10 |
| GEMINI-20261009-03 | Low | `server/main.py` | Encounter simulation does not aggregate repeated enemy names into instance counts | FIXED | Repeated names (by matched id) raise one entry's count; test added. Commit 3c5cedd | 2026-10-10 |
