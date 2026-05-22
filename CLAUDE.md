# CLAUDE.md

The canonical rules for AI coding agents working on this repository
live in [`AGENTS.md`](AGENTS.md). Read it first. Everything below is
Claude-specific supplement, not override.

## Claude-specific notes

- Per-user Claude Code memory under `~/.claude/projects/<project>/`
  is your working knowledge — saved context from prior sessions
  (user preferences, decisions, project state). It **complements**
  `AGENTS.md`; it does not override it. If a memory entry conflicts
  with `AGENTS.md`, `AGENTS.md` wins and the memory entry should be
  updated.
- The user's global instructions (their personal `~/.claude/CLAUDE.md`)
  may impose additional rules — most commonly around when not to push
  or commit without explicit approval. Respect those on top of the
  project rules.
- The "no fixes without understanding" discipline in `AGENTS.md` is
  load-bearing for this project. It exists because the cost of
  speculating about firmware behaviour vs. probing the actual device
  is silent parser breakage that unit tests against synthetic data
  will not catch. Treat it as a hard constraint, not advisory.
- The `main` branch ruleset has admin-bypass enabled, which means
  `gh pr merge --admin` would technically succeed if a human's gh
  token is authenticated. Do not use it. Bypass is the maintainer's
  emergency tool, not an agent's convenience.
