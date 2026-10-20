# 0001-pure-domain-module

## Status
Accepted

## Context
The parcel lifecycle (dispatch, fail, retry, return) is the core business rule of
Tawseel's dispatch backend. Sherif is still learning the exact rules, so they will
change during the sprint. If the lifecycle logic were scattered across the HTTP
route handlers, every rule change would require touching request parsing, response
shaping, and error handling code, increasing the risk of regressions and making the
rules hard to test in isolation.

## Decision
The lifecycle lives in a single pure Python module, `app/domain.py`, with no
imports from FastAPI, Starlette, Pydantic, or SQLAlchemy. `Parcel` is a frozen
dataclass and `apply_event(parcel, event)` returns a new `Parcel` without mutating
its input. The HTTP layer in `app/api.py` is the only caller of `apply_event`.

## Consequences
- Business rules can be unit-tested quickly without starting the HTTP server or a
  database, and property-based tests (e.g. Hypothesis) can generate random event
  sequences against a plain Python function.
- Rule changes are confined to `app/domain.py`; the HTTP layer stays stable.
- The domain module has no framework coupling, so it can be reused by workers,
  CLIs, or other services without pulling in the web stack.
- The HTTP layer must translate domain `InvalidTransition` into HTTP 409 responses
  itself, adding a small translation layer.
