# Contributing to Kompany

Thanks for your interest in contributing. This project is open to improvements across the engine, agents, interfaces, and documentation.

## How to Contribute

### Reporting Issues

- Use [GitHub Issues](https://github.com/Fei2-Labs/Kompany/issues) for bugs, feature requests, and questions
- Include your company stage profile and the directive that triggered the issue
- Include the full error output if applicable

### Contributor License Agreement (CLA)

Kompany Core is AGPL-3.0 and dual-licensed: the same code also ships in
commercially licensed builds for [Kompany Pro](docs/why-agpl.md) subscribers.
For your contribution to be includable in both, we ask every contributor
to sign a lightweight CLA (one-time, via the CLA bot comment on your
first PR). You keep your copyright; you grant Kompany the right to
license your contribution under the project's licenses. PRs cannot be
merged before the CLA is signed. Rationale:
[ADR-0004](docs/adr/0004-agpl-relicense.md).

### Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Set up the dev environment (first time only):
   `cd kompany && python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[api,mcp,dev]"`
4. Make your changes
5. Run tests: `cd kompany && source .venv/bin/activate && python -m pytest tests/ -v`
6. Commit with a clear message
7. Push and open a PR against `main` (the CLA bot will prompt you on your first PR)

### What We're Looking For

- New agent personas or refinements to existing soul.yaml files
- Additional execution subagents
- Cost optimization strategies
- Memory system improvements
- New interface adapters (Slack, Discord, etc.)
- Documentation improvements

### Support the Project

If you find Kompany useful, consider [buying me a coffee](https://buymeacoffee.com/clarezoe).
