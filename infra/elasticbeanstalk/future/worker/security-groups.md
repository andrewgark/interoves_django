# Future worker network plan

- Worker instances are private and have no public ALB/listener.
- Worker security group permits egress to RDS, Redis, SQS/Secrets Manager
  endpoints and application dependencies.
- RDS security group permits MySQL only from web and worker security groups.
- Redis security group permits Redis only from web and worker security groups.
- The HMAC secret is stored in Secrets Manager and injected only into the
  worker role; it is never committed or logged.
