# AI Usage

## Tools
- Kilo (CLI coding assistant): used to explore repository conventions, draft the
  FastAPI application, domain model, tests, ADRs, and AI_USAGE.md, and to run the
  test suite.

## What AI got wrong
- Initially proposed PowerShell `&&` separators which PowerShell does not accept;
  fixed by using the `workdir` parameter and separate commands.
- Placed `from pydantic import BaseModel, Field` inside `create_app()` instead of
  at module top; moved it to the imports.
- Made the `event_in` body parameter optional with a default of `None`, which
  would have allowed a missing body to bypass the required-body 422; made it
  required so FastAPI returns 422 for a missing body too.

## In my own words
- Why does listParcels need a cursor rather than an offset? Because parcels can
  change state between pages; an offset into the filtered list would skip or
  repeat items after a state change, while a cursor positions into creation order
  and re-applies the filter at scan time so every parcel is seen exactly once.
- Which of your four operations would break the courier app if you renamed one
  field, and who would have to approve that change? Renaming any field in the
  parcel JSON (for example `merchant_ref`) would break the courier app, which
  builds against the contract; the change needs approval from the courier app
  team and the merchant integration team (the contract consumers) before
  deployment.
