# AWS Infrastructure

Terraform is pinned to `>= 1.10` because the remote S3 backend uses native lock files. Terraform 1.5.7 cannot initialize this configuration.

## Bootstrap Remote State

```bash
terraform -chdir=infra/bootstrap init -backend=false
terraform -chdir=infra/bootstrap apply
terraform -chdir=infra/bootstrap init -migrate-state
```

## Provision Foundations

```bash
terraform -chdir=infra/environments/prod init
terraform -chdir=infra/environments/prod plan
terraform -chdir=infra/environments/prod apply
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
terraform -chdir=infra/environments/prod apply \
  -var='api_image_tag=<immutable-tag>' \
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
TERRAFORM_BIN=terraform make deploy-dashboard
```

Read the deployed URLs and verify the public surfaces with:

```bash
terraform -chdir=infra/environments/prod output -raw dashboard_url
terraform -chdir=infra/environments/prod output -raw api_service_url
curl "$(terraform -chdir=infra/environments/prod output -raw dashboard_url)/api/public/status"
curl "$(terraform -chdir=infra/environments/prod output -raw dashboard_url)/api/public/challenger"
```
