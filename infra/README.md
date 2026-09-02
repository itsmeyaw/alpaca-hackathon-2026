# AWS Infrastructure

OpenTofu is pinned to `>= 1.10` because the remote S3 backend uses native lock files.

## Bootstrap Remote State

```bash
tofu -chdir=infra/bootstrap init -backend=false
tofu -chdir=infra/bootstrap apply
tofu -chdir=infra/bootstrap init -migrate-state
```

## Provision Foundations

```bash
tofu -chdir=infra/environments/prod init
tofu -chdir=infra/environments/prod plan
tofu -chdir=infra/environments/prod apply
```

Terraform creates only the secret container. Load Alpaca values outside Terraform so credentials never enter state:

```bash
aws secretsmanager put-secret-value \
  --secret-id catalyst-router/prod/runtime \
  --secret-string file://runtime-secret.json
```

Do not commit `runtime-secret.json`.

The read-only API runs on App Runner and the static dashboard is delivered from the private
dashboard bucket through CloudFront. Deployment requires an immutable ECR image tag and an
immutable challenger run ID:

```bash
tofu -chdir=infra/environments/prod apply \
  -var='runtime_image_tag=<immutable-tag>' \
  -var='challenger_run_id=<run-id>' \
  -var='challenger_manifest_sha256=<manifest-sha256>' \
  -var='bedrock_model_id=<model-or-inference-profile-id>' \
  -var='bedrock_model_arn=<exact-model-or-inference-profile-arn>'
```

The App Runner service injects the runtime secret as `ALPACA_CREDENTIALS`, parses its
`ALPACA_KEY` and `ALPACA_SECRET` fields, and reconciles against only the Alpaca Paper Trading API
at `https://paper-api.alpaca.markets/v2`. It loads and verifies the private S3 manifest and model
at startup. The API exposes only sanitized model metadata. Model execution defaults to shadow-only.
Set `model_paper_execution_enabled=true` only for an artifact matching the ADR-0013 15-minute
contract; runtime rejects this override for five-minute artifacts. Bedrock access is restricted to
the exact configured ARN. The task retains HTTPS egress because it must reach AWS and Alpaca APIs.

Build, deploy, and invalidate the dashboard with Terraform:

```bash
make deploy-dashboard
```

Read the deployed URLs and verify the public surfaces with:

```bash
scripts/verify-public-api
```
