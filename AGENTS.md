## Cursor Cloud specific instructions

This is a polyglot Rust + Python project. The Taskfile (`Taskfile.yml`) orchestrates all build/test/demo commands—see it and `README.md` for the full reference.

### Quick reference

| Action | Command |
|---|---|
| Build extension | `task build` (or `cargo build --release`) |
| Rust unit tests | `task test:rust` (or `cargo test`) |
| Python integration tests | `task test:python` (or `uv run pytest -v`) |
| All tests | `task test` |
| Demo server | `task demo` (runs at `http://127.0.0.1:8000/`) |

### Gotchas

- **Rust version**: The project's transitive dependencies require Rust ≥ 1.88 (edition2024). The pre-installed Rust may be too old; if `cargo build` fails mentioning `edition2024`, run `rustup update stable && rustup default stable`.
- **`protoc` is required at Rust compile time**: `build.rs` invokes `protoc` to compile `proto/test.proto`. If `protoc` is missing, `cargo build` will fail.
- **Demo settings auto-detect the local `.so`**: `demo/demo_project/settings.py` looks for `target/release/libsqlite_protobuf.so` automatically—no env var needed when running from the repo root after `task build`.
- **Demo needs its own `uv sync`**: The demo app at `demo/` has a separate `pyproject.toml` and virtualenv. `task demo:setup` handles descriptor compilation, `uv sync`, and Django migrations.
- **Root `uv sync` is for tests only**: The root `pyproject.toml` covers `pytest`, `protobuf`, and `django` for integration tests. The demo has its own dependency set.
