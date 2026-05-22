<!--
PR template — see AGENTS.md for the full contribution guide and
CONTRIBUTING.md for the human-facing dev-setup pointers.

Do not remove sections. Mark sections "n/a" with a one-line reason
when they truly don't apply (e.g. "n/a — docs-only"). Reviewers use
this template to track what was tested and what's at risk; removing
parts of it makes review harder.
-->

## Summary

<!--
1–3 bullets on what changed and why. Focus on the *why*; the diff
shows the *what*. Link to an issue or design discussion if one
motivated the change.
-->

-

## Test plan

<!--
For code changes, the `make test` box must be checked. CI runs
hassfest, HACS validate, and CodeQL on push — reference them only
if you've manually triggered them or if there's something specific
to call out. For docs-only PRs, write "n/a — docs only" below the
checklist and leave the box unticked.
-->

- [ ] `make test` — pytest suite passes locally

**Manual / live verification:**

<!--
For changes that touch the parser, AQI math, or entity tree shape:
how you exercised them. If you ran the change against a real PA
sensor (via `make ha-up` or a standalone script), say which sensor
(indoor / outdoor / borrowed) and what you observed. If you couldn't
test a surface, say so explicitly so a reviewer can pick it up.
-->

## Risk

<!--
Anything a reviewer should think hardest about. Parser changes
(firmware-quirk tolerance), AQI breakpoint or correction math,
channel-disagreement logic, entity-tree shape (renaming unique_ids
will reset users' history), options-flow schema migrations. "Low —
internal refactor with full test coverage" is a valid answer when
true.
-->

## Docs touched

<!--
Tick the docs you updated in this PR, or write "n/a — internal
change" if no user-visible behaviour or future plans changed.
-->

- [ ] `README.md`
- [ ] `DESIGN.md`
- [ ] `TODO.md`
- [ ] n/a — internal change
