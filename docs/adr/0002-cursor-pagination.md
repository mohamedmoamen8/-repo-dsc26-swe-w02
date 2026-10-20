# 0002-cursor-pagination

## Status
Accepted

## Context
`listParcels` must page through parcels in creation order, with an optional state
filter and a default limit of 20. Offset pagination (`limit` + `offset`) was
considered, but it fails when items change state between requests: if page 1
returns the first 10 `created` parcels and those are dispatched before page 2 is
fetched, an offset-based `state=created` query would skip parcels 11–20 (the
offset now lands in a different position of the filtered list). The courier and
merchant clients must see every parcel exactly once during a walk, even as states
change.

## Decision
`listParcels` uses an opaque cursor instead of an offset. The cursor encodes the
creation sequence of the last parcel returned on the previous page. The next page
resumes scanning creation order from after that sequence and applies the state
filter at scan time. `next_cursor` is `null` on the page that contains the last
matching item. Cursors the server did not issue (garbage) are rejected with 400.

## Consequences
- Paging with a state filter never skips or repeats parcels when earlier items
  change state between requests, because the cursor positions into creation order
  rather than into the filtered result set.
- The cursor is opaque to clients; they only echo back `next_cursor`.
- A cursor walk requires a server-side scan per page, which is acceptable for the
  expected parcel volumes in Sprint 0.
- Clients cannot jump to an arbitrary page number; they must walk sequentially.
