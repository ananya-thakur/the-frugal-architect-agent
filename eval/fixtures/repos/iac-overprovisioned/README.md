# iac-overprovisioned (eval fixture)

Synthetic CDK stack used by the eval-harness `tool-iac-firm` and `ground-iac-repo`
test cases.

**Known violations (planted intentionally):**
- Law I: no cost-allocation tags or billing alerts
- Law IV: no CloudWatch alarms or detailed monitoring
- Law V: always-on r6g.2xlarge EC2 for a 2 h/day workload
- Law VI: RDS at maximum allocation with no scaling plan

All account IDs are synthetic (123456789012).
