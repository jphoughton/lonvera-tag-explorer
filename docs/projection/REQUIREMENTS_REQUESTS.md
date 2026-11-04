# Requirements requests (agents never edit requirements.txt; the orchestrator applies these)

| Requested by | Package | Version tested | Why | Needed when |
|---|---|---|---|---|
| feeds | `cryptography` | 50.0.2 (the dev venv) | Fernet encryption of the ExpandFi OAuth refresh token stored in `feed_oauth` (`etl/feeds/expandfi_auth.py`). Imported lazily: every other feed runs without it. | Before the ExpandFi spike runs on Railway (`login` runs on Josh's Mac with the dev venv, which already has it) |
