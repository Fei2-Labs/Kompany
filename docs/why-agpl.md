# Why AGPL?

**TL;DR: If you're a founder running Kompany for your own company, AGPL
costs you nothing. The license only bites if you take this engine and
sell hosted access to it as your own product.**

## What you can do, free, forever

- Run Kompany for your own company — solo, startup, agency, whatever.
  Using it internally (even commercially, even to make money) triggers
  no obligations. You are a *user*, not a *distributor*.
- Modify the engine for your own use. Private modifications you don't
  distribute or host for others stay private.
- Fork it, study it, package it, redistribute it — under the same
  AGPL-3.0 terms.

## What requires either sharing or a commercial license

One thing: **offering Kompany (or a modified Kompany) to other people
as a network service** — a hosted "AI company" SaaS built on this
engine. Under AGPL §13 you must publish your modified source to your
users. If you don't want to do that, [contact us](mailto:vatztrd6809@hotmail.com)
for a commercial license.

That's the whole deal. It exists so that a well-funded team can't take
the engine, close it, and sell it back to the community as their own
cloud — the failure mode that pushed Grafana, Quickwit, Windmill, and
Cal.com to the same license.

## Why not Apache/MIT?

We considered it (the project briefly was Apache-2.0, pre-release).
Permissive licensing has exactly one failure mode for a project like
this: a hosted competitor with more capital and zero obligation to
contribute back. AGPL closes that while leaving every founder use case
untouched. And switching *later* — after a community exists — is the
Elastic/HashiCorp betrayal script. We'd rather be honest on day one.

## How this interacts with Kompany Pro

Kompany (the company) holds the copyright and dual-licenses:

- **Core** (this repo): AGPL-3.0 for everyone.
- **Pro** (`kompany-pro`): proprietary plugin package loaded through
  Core's [plugin contract](context/plugin-contract.md). Pro subscribers
  receive a commercial license covering their private use of the
  combined work, so AGPL obligations never propagate to them.

External contributions to Core require a lightweight CLA (see
[CONTRIBUTING](../CONTRIBUTING.md)) so contributions can be included in
both the AGPL and commercially licensed builds.

## FAQ

**I'm building a closed-source product and want my Kompany agents to
help me build it.** Fine. The license covers the engine's code, not
what the engine produces. Output of your agents is yours.

**I run Kompany for my agency's internal ops and bill clients for the
work.** Fine. You're not giving clients network access to the engine.

**I want to offer Kompany-as-a-service to my customers.** That's the
one case: AGPL (publish your modifications) or commercial license.

**Does AGPL apply to my company data / vault / mission?** No. Your
data is yours; the license covers the engine source code only.

Decision record: [ADR-0004](adr/0004-agpl-relicense.md).
