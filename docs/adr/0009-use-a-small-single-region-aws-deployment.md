# ADR-0009: Use a Small Single-Region AWS Deployment

- Status: Accepted
- Date: 2026-08-27

## Context

The agent needs an always-on WebSocket process during a one-week competition, durable reconciliation state, a public dashboard, private operator controls, and local reproducibility. Cost should remain low without sacrificing deterministic recovery.

## Decision

Deploy in one AWS region with Terraform:

- one Python 3.14 FastAPI/agent workload on ECS Fargate with a single active trading replica;
- DynamoDB for operational state, idempotency records, decisions, orders, positions, policies, and reporting projections;
- S3 for raw market-observation archives and analytical exports;
- a statically built Next.js TypeScript dashboard on S3 and CloudFront;
- an Application Load Balancer for the public reporting API and authenticated operator API;
- Cognito for operator authentication;
- Secrets Manager for Alpaca and model credentials;
- CloudWatch logs, metrics, and alarms; and
- an S3 Terraform backend using state locking and encryption.

Use `us-east-1` by default. Keep Alpaca integrations behind interfaces so local development can run against recorded data and the paper environment.

## Consequences

- The single trading replica avoids competing stream consumers and duplicate orders.
- Startup must reconcile durable state with Alpaca before entering `RUNNING` mode.
- The dashboard remains independently cacheable and cannot directly mutate trading state.
- Serverless persistence avoids database instance cost and removes relational database operations from the one-week deployment.
- Python 3.14 remains the default; downgrade only if a required production dependency demonstrably lacks support.
