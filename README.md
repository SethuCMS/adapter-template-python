# SethuCMS adapter template (Python)

Start here to write an adapter for a database in Python. You write one class; this template
turns it into the gRPC server the SethuCMS gateway calls (`proto/adapter.proto`).

```
src/my_adapter/
  base.py        the contract you implement (plain Python types) and the rules to follow
  example.py     a complete working adapter (in memory). Copy it and replace the storage
  servicer.py    protocol glue: safe error handling, one adapter per connection. Usually untouched
  server.py      starts the server (localhost by default; TLS / mutual TLS for anything else)
  generated/     code generated from proto/adapter.proto. Do not edit
tests/           talks to the real server over a local socket, the way the gateway does
```

## Make your own adapter

1. Rename the folder `src/my_adapter` and the name in `pyproject.toml` (for example `sethucms_adapter_mysql`).
2. Copy `example.py`, rename the class, and implement the methods with your database driver.
3. In `server.py` replace `InMemoryAdapter` and `"memory"` with your class and type name.
4. Run `pytest`. Add tests for your database (a throwaway instance, never real data).

Read the rules at the top of `base.py` before you write any query code. The short version:
**parameters only, never paste text into a query; only use names from introspection; be honest in
`capabilities()`; never copy driver error text into an error; honour `read_only`.**

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
my-adapter                       # listens on 127.0.0.1:50051
```

Settings (environment variables): `ADAPTER_ADDRESS` (default `127.0.0.1:50051`).
For a non-local address you must provide TLS; the server refuses to start without it:
`ADAPTER_TLS_CERT`, `ADAPTER_TLS_KEY` (PEM files) and `ADAPTER_CLIENT_CA` to require the
gateway's client certificate (mutual TLS, recommended).

## Good to know

- **Numbers:** the protocol carries numbers as doubles. Whole numbers come back to you as
  `int`; values above 2^53 are not exact, so send those as strings.
- **Cursors:** return any string as `next_cursor` (an offset, a key, a token). The gateway
  signs it and checks it, so a client cannot forge one. Return an empty string on the last page.
- **Credentials** arrive in `connect()` only. Do not store them anywhere except the live connection.
- **Unexpected exceptions** are logged on your machine and reported to clients as a generic
  `INTERNAL` error. Raise `AdapterError` for failures you expect.
- `Batch` and `Subscribe` are optional in the protocol; the template reports them as unsupported.

## When the protocol changes

Copy the new `adapter.proto` from `sethucms-docs/schemas/` into `proto/`, then run `sh scripts/generate.sh`.

## Before you release

The adapter must pass the same behaviour checks as the first-party adapters: filters, sorting,
every record exactly once across pages, forged cursors rejected, read-only really read-only,
hostile input treated as data. The tests in `tests/test_adapter.py` are the model to copy.
