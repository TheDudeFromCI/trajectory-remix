# Repository guidance for agents

For codebase maintenance sweeps, read [docs/MAINTENANCE.md](docs/MAINTENANCE.md).
It contains the review checklist, subsystem invariants, validation commands, and
handoff/PR expectations. Use [docs/TESTING.md](docs/TESTING.md) for test coverage
details.

## Documentation style

User-facing docs (`README.md`, most of `docs/`, and `examples/README.md`) are written
for human readers: plain language, a short intro saying what the page is for, task-
oriented headings, tables for options and routes, and examples before edge cases.
When you change a doc's content, **preserve that style**. Edit in place rather than
replacing it with dense, agent-oriented prose, and keep exhaustive implementation
detail in the "For developers" sections or in contributor docs
([MAINTENANCE](docs/MAINTENANCE.md), [TESTING](docs/TESTING.md),
[ACCEPTANCE](docs/ACCEPTANCE.md)). Keep existing filenames and heading anchors that
other pages link to, and update every link if you move content.
