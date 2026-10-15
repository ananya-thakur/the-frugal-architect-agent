# app-antipatterns (eval fixture)

Synthetic application service used by the eval-harness `tool-app-possible`
test case.

**Known anti-patterns (planted intentionally):**
- N+1 queries (fetches items per order in a loop)
- Missing pagination (loads all orders unconditionally)
- Full table scan (LIKE with leading wildcard)
- Per-request DB connection initialization (no pool)
- Synchronous polling instead of event-driven (SQS/SNS)

All findings from this repo should carry POSSIBLE confidence (app-code scanner).
