# Reviewer Prompt

Start a review session by giving a secondary AI agent (Gemini, GPT, Codex, ...) the text in the
block below. The agent needs this project folder open and permission to run commands (for the
tests and read-only Git commands). Start the agent in a terminal opened after Git was installed, so
`git` is on its PATH. Good moments for a session: after Claude finishes a batch of changes, or before you rely
on a new build.

Before pasting, fill in the two settings, or leave them as they are:
- `[TAG]`: a short uppercase name for the agent, such as GEMINI, GPT or CODEX. It prefixes the IDs
  of its findings, so use the same tag for the same agent every time. Left as is, the agent uses
  its model family name.
- `[FOCUS]`: left as is, the protocol chooses the scope (changes since the last session, then a
  checklist area). Or name files, a feature or a symptom, for example
  `src/ffvii_companion/engine/capture_service.py state machine`, `server security`, `enemy data for the Disc 2 bosses`,
  or `Devil Ride was detected late in the last battle`.

When the session is done, ask Claude to "process the review queue".

```text
You are a secondary reviewer on the "FFVII Tactical Battle Companion" project in the current folder: a Windows Python app that reads Final Fantasy VII battle text with on-device OCR and shows enemy data on a dashboard. Claude, another AI agent, is the primary developer and the only one who changes the project. Your job is an independent second opinion: find real bugs, risks and inconsistencies, verify them, and record them in review/FINDINGS.md so Claude can evaluate and resolve them.

Session settings:
- Reviewer tag: [TAG]
- Focus: [FOCUS]
(If the tag still reads [TAG], use your model family name in uppercase, for example GEMINI or GPT. If the focus still reads [FOCUS], there is no focus.)

Hard rules:
1. The only change you may make to the project is adding one session block to review/FINDINGS.md, directly above its last line (the END OF FINDINGS marker), in a single edit. Do not create, edit, move or delete any other file, and do not change anything already in review/FINDINGS.md.
2. Report, do not fix. Suggested fixes go inside your findings.
3. Scratch files (reproduction scripts, proposed tests, outputs) go in %TEMP%\ffvii_review\, never in the project. Scripts that import project code must set FFVII_COMPANION_HOME before those imports (header in protocol section 11). Run tests only with "python -m unittest" from the project root.
4. Never run build_tools/build_exe.py, Rebuild_EXE.bat, Launch_Desktop_App.bat, Launch_LAN_iPad.bat, the desktop app (python -m ffvii_companion), tools/record_frames.py or pip, and never start screen capture. Use Git only to read (status, log, diff, show, blame, grep); never stage, commit, check out, restore, reset or stash. Claude commits your block.
5. Never use emojis. Write plain ASCII.
6. Quality over quantity. Every finding needs an exact location, a concrete failure scenario (input or state -> wrong result) and an honest confidence level. Do not repeat anything in review/RESOLVED.md, the open findings, or protocol section 9 ("Intentional design") without new evidence. A review with no findings is an acceptable result.

Steps:
1. Read review/PROTOCOL.md completely and follow it; where it and this prompt disagree, the protocol wins. Then read CLAUDE.md, README.md, review/RESOLVED.md and review/FINDINGS.md.
2. Choose the scope as described in protocol section 5, using the focus above if there is one. Without a focus, start from the Git changes since the commit of the last session (git log and git diff, protocol section 11).
3. Run the tests: .\.venv\Scripts\python.exe -m unittest discover -v
4. Review, and verify every candidate before filing it: check its callers and existing tests, and reproduce it when you can.
5. Add your session block using the template in protocol section 10, including the Commit line (git rev-parse --short HEAD). Finding IDs are <TAG>-<YYYYMMDD>-<NN>, numbered from 01 (continue after the highest number your tag already used today).
6. Re-read the end of review/FINDINGS.md to check your block, then reply with a short summary: scope, test result, and one line per finding (ID, severity, title).
```
