# AGENTS.md — sethucms-adapter-template-python

Template for an adapter written in Python (gRPC).

This repository is one part of SethuCMS. **Read `../sethucms/AGENTS.md` first** for the whole picture, the security rules that must not be broken, and the working agreements.

## Build and test

```sh
pip install -e ".[dev]" && sh scripts/generate.sh && python -m pytest
```

## Notes

Protocol: proto/adapter.proto (must match sethucms-docs/schemas/adapter.proto). Regenerate code after any protocol change.

## Always

- Run the tests for what you changed and say what you ran and what you could not run.
- Do not `git commit` or `git push` unless the owner asks.
- Never print or store a secret, token or password.
