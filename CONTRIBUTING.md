# Contributing

Thanks for helping! A few rules keep the engine easy to work on.

## Setup and tests

```bash
pip install -e ".[dev]"
python -m pytest -q                       # all tests (integration tests need FFmpeg ≥ 7)
python -m pytest -q -m "not integration"  # fast unit tests
ruff check .                              # lint; CI runs it with the full suite
```

- Every change comes with a test. Tests run in a temporary workspace (`tests/conftest.py`); never
  write outside `tmp_path`, and never commit media (`*.mp4`, `*.mov`, `*.wav`…) — integration tests
  generate synthetic clips with FFmpeg.
- Keep `docs/LEGACY_KEYS.md` in sync with `adapters/files/legacy_es.py` (a test checks it).

## Style

- **English everywhere**: code, comments, log messages, CLI help, docs and file keys. Spanish only
  appears as backward-compatible aliases in `legacy_es.py` and as `language: es` content defaults.
- Respect the layers (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)): `domain/` is pure and
  imports nothing from the other layers; anything that touches FFmpeg, files or models goes behind a
  port in `ports/` with its adapter in `adapters/`, wired in `container.py`.
- Python ≥ 3.11, type hints, small functions, docstrings that explain *why*. Lines up to ~120
  characters.
- New plan or recipe keys: give them a default in `recipes/base.yaml` (commented) and document them
  in [docs/PLAN_FORMAT.md](docs/PLAN_FORMAT.md).

## Pull requests

Describe what changes for the user, how you tested it, and any new keys or commands. Don't include
personal footage, paths or project names in code, tests or docs.
