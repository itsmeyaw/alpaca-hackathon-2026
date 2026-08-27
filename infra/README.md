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

Do not commit `runtime-secret.json`. Networking, CloudFront, the ECS task definition, and the service are deliberately deferred until the read-only image and reconciliation flow are deployed from immutable artifacts.
