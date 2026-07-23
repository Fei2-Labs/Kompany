"""Channel adapters (06-12-channels).

A channel adapter translates transport messages into a shared
``DirectiveContext`` and calls ``engine.process_directive``. Adapters never
reason — no LLM calls live in this package; the engine does all the
thinking. See the internal design spec.
"""
