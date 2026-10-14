# Contributing

Thanks for helping. Bug reports, misdetection reports and corrections to the enemy data are the most useful contributions.

## Reporting a wrong detection

When the companion shows the wrong enemy or count, press **REPORT** on the dashboard. It saves the capture region with the text the OCR read into the `reports` folder next to the EXE ([details](README.md#reporting-a-wrong-detection)). Open an issue, describe what the game showed, and attach the report folder as a zip: its frames can become a regression test.

## Reporting a bug or wrong data

Open an issue with the version (in the name of the release zip you downloaded, or the commit when you run from source), what you did, what you expected and what happened. For wrong enemy data or a wrong tactical note, name the enemy and your source. Security problems: see [SECURITY.md](SECURITY.md).

## Changing the code

1. Set up the source as described in [Development](docs/DEVELOPMENT.md#setup), and read [Architecture](docs/ARCHITECTURE.md) first: it explains how the parts fit together and which unusual choices are deliberate.
2. Make the change on a branch, with a test for it. Keep the docs in step: `README.md` for what a player sees, `docs/` for configuration, internals and development.
3. Run the whole suite: `.\.venv\Scripts\python.exe -m unittest discover -v`. GitHub Actions runs it on every push and pull request.
4. Open a pull request that says what changed and why.

Most of the game data in `src/ffvii_companion/data/` is generated from the game's files: change the generator, not the JSON, except for the curated fields (`id`, `name`, `notes`, `ai_summary`). See [Game data tools](docs/DEVELOPMENT.md#game-data-tools).

By contributing you agree that your contribution is released under the project's [MIT License](LICENSE).
