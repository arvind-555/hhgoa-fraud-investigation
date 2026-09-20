# config/

Non-secret configuration and templates only.

- `tigergraph.example.env` — the variables the build phase will read. Copy to `.env` (repo root, git-ignored) and fill in.
- Real credentials, tokens, secrets and any `*.local.*` files are git-ignored (see the root `.gitignore`) and must never be committed.
- Tunable analysis/agent parameters (ring thresholds, hub thresholds) will be added here as versioned, secret-free files
  once the build phase starts; their reference values are in `docs/implementation_spec.md` §5–§6.
