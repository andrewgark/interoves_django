# Future alarm plan

These are definitions for the future dashboard/alarm stack, not applied alarms.

## Web

- `CPUUtilization`: warning >65%/5m, critical >80%/5m.
- `mem_used_percent`: warning >75%/10m, critical >85%/5m.
- ALB target 5xx: warning >1%/5m, critical >5%/2m.
- `UnHealthyHostCount >= 1`: alert.
- healthy web host count `< 2`: critical.
- ALB target response p95: warning >1s, critical >3s.

## Worker and SQS

- worker CPU: warning >75%/15m, critical >90%/5m.
- worker `mem_used_percent`: warning >75%, critical >85%.
- `ApproximateAgeOfOldestMessage`: warning >120s, critical >600s.
- DLQ message count `>=1`: alert.
- task failure rate: warning >1%, critical >5%.
- task duration p95: warning >5s, critical >15s.

## Dependencies

- RDS `DatabaseConnections`: warning >=40, critical >=48.
- RDS CPU: warning >70%/10m, critical >85%/5m.
- Redis EngineCPU: warning >65%, critical >80%.
- Redis memory: warning >70%, critical >85%.
- Redis evictions `>0`: alert/investigate.

Latency thresholds must be compared with the production baseline before enabling
paging alarms.
