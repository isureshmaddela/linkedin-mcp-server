# Infrastructure as code

`main.tf` declares an S3 bucket for encrypted tenant documents (versioned, KMS-encrypted, TLS only, no public access, noncurrent versions expire after 35 days), a KMS key, and an IAM policy scoped to the `tenant/` prefix. `terraform init -backend=false`, `validate` and `fmt -check` pass (Terraform 1.9), checkov reports 78 passed and 8 skipped (each skip has its reason in `main.tf`), and tflint with the AWS ruleset is clean. `plan` and `apply` were **not** run (they need AWS credentials): run them in your own account and review the result before applying.

```bash
cd deploy/terraform
terraform init
terraform plan -var bucket_name=<globally-unique-name>
```

What it does not cover: networking, a Kubernetes cluster, a database (the control plane is a SQLite file on a shared volume, see [../../docs/multitenant.md](../../docs/multitenant.md)), DNS and certificates, the shared read-write-many volume the manifests in `../k8s/` expect, a container registry, secrets storage, the Stripe webhook, SMTP, or the service account's access keys (create them outside Terraform so they do not land in state).

The KMS key encrypts the bucket and is the key the application's KMS master-key provider (`AIRA_CLOUD_KMS=aws`, `AIRA_CLOUD_KMS_KEY_ID`) wraps per-organization data keys with; the access policy grants `kms:Encrypt`, `kms:Decrypt` and `kms:GenerateDataKey`. S3 server access logs go to a second bucket.

The `environment` output lists the storage variables for the application (see [../../docs/multitenant-operations.md](../../docs/multitenant-operations.md) for the full reference).
