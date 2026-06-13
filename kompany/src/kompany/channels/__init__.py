"""Channel adapters (06-12-channels).

A channel adapter translates transport messages ⇄
``engine.process_directive(text, session_id)`` (PRD D1). Adapters never
reason — no LLM calls live in this package; the engine does all the
thinking. See the internal design spec.
"""
