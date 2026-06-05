# Drift Log

Append-only rollup for cross-lane docs, wording, and status drift.

When a worker reports drift in its evidence bundle on files outside its lane write scope, the integrator or coordinator appends the item here under the originating thread. Lane C cycles consume open drift items in their scope and close them by deleting the resolved lines when the relevant doc is updated.

This log is not a TODO list for code work. If an item is actually a contract gap, escalate it to a new task brief instead of storing it here.

## Open Drift
