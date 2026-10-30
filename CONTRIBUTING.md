# Contributing

Thanks for improving Gptsovit Auto.

## Development setup

1. Use Python 3.9+ (the GPT-SoVITS bundled runtime is also fine).
2. Install dependencies: `pip install -r requirements.txt`.
3. Run the test suite:

   ```bat
   "%GPT_SOVITS_HOME%\runtime\python.exe" -B -m unittest discover -s tests -v
   ```

   Or, with a normal Python installation:

   ```bat
   python -B -m unittest discover -s tests -v
   ```

4. Keep a GPT-SoVITS installation available for the integration tests. Tests
   that read presets/weights are skipped or fail with a clear path error when
   the install is missing.

## Rules

- Never write into the GPT-SoVITS installation directory. Use the workspace
  config copy and `-B` / `pycache_prefix`.
- Keep Windows path handling (spaces, non-ASCII, long paths) working.
- Add a unit test for parser, naming, voice-editor, or settings changes.
- Do not commit `config/settings.yaml`, `voices/voices.yaml`, `state/`,
  `logs/`, `cache/`, or `tmp/`; they contain machine-specific data.
- Preserve the existing public CLI unless a change is discussed first.
- For UI changes, run `ui_main.py`/`run_ui.bat` and exercise preview, start/stop,
  and one voice add/remove; keep Tkinter work on the main thread and long work in
  the worker thread.
- For drag-and-drop changes, test with an actual `.md` file on Windows (or send a
  synthetic `WM_DROPFILES`); for random takes, check the `↳` rows and the
  `_随机NN` filenames without relying on the project manifest.
- Do not add copyrighted character names, model download links, reference audio,
  or model files to the repository. Use A/B/C/D placeholders and tell users to
  download/configure their own models.

## Pull requests

Describe the user-facing behavior change, the test command you ran, and any
GPU/model requirements. Screenshots are useful for wizard changes; redact
local paths and personal voice data.
