# Infrastructure for the hosted build: an object-storage bucket for encrypted documents, an access-log bucket and a KMS key.
# `terraform init -backend=false`, `validate` and `fmt -check` pass (Terraform 1.9, AWS provider 5.x) and checkov reports no
# unaccepted findings; see docs/multitenant-operations.md. `plan` and `apply` need AWS credentials and were not run.
#
# The KMS key encrypts the bucket at rest and is the key the application's KMS master-key provider
# (AIRA_CLOUD_KMS=aws, AIRA_CLOUD_KMS_KEY_ID) wraps per-organization data keys with. Documents are already encrypted by
# the application with per-organization keys before they are uploaded.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "region" {
  type        = string
  description = "One region per deployment (AIRA_CLOUD_REGION should name it)."
  default     = "eu-west-1"
}

variable "bucket_name" {
  type        = string
  description = "Globally unique bucket name for tenant documents."
}

variable "log_retention_days" {
  type        = number
  description = "How long S3 access logs are kept."
  default     = 90
}

variable "noncurrent_version_days" {
  type        = number
  description = "How long overwritten or deleted objects stay recoverable (a backup window for documents)."
  default     = 35
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "current" {}

# Key policy: the account administers the key through IAM (the standard root statement). The application's
# identity gets only what aws_iam_policy.app grants it below.
data "aws_iam_policy_document" "kms" {
  # checkov:skip=CKV_AWS_109: in a key policy "*" means this key; the root statement is what makes IAM policies effective.
  # checkov:skip=CKV_AWS_111: same statement.
  # checkov:skip=CKV_AWS_356: same statement.
  statement {
    sid       = "EnableIamPolicies"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
}

resource "aws_kms_key" "documents" {
  description             = "Aira documents bucket and master-key wrapping"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.kms.json
}

resource "aws_kms_alias" "documents" {
  name          = "alias/aira-documents"
  target_key_id = aws_kms_key.documents.key_id
}

resource "aws_s3_bucket" "documents" {
  # checkov:skip=CKV_AWS_144: one region per deployment is a data-residency requirement; copy to another region in your own account if you accept that.
  # checkov:skip=CKV2_AWS_62: nothing consumes bucket events; the application reads and writes objects directly.
  bucket = var.bucket_name
}

# Access logs for the documents bucket go to a separate bucket.
resource "aws_s3_bucket" "logs" {
  # checkov:skip=CKV_AWS_144: logs stay in the deployment's region.
  # checkov:skip=CKV_AWS_18: this is the log destination; logging it would only log the logging.
  # checkov:skip=CKV2_AWS_62: nothing consumes bucket events.
  bucket = "${var.bucket_name}-logs"
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.documents.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    id     = "expire-access-logs"
    status = "Enabled"
    filter {}
    expiration {
      days = var.log_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Server access logging delivers through the S3 logging service principal (no ACLs).
data "aws_iam_policy_document" "logs" {
  statement {
    sid       = "S3ServerAccessLogsDelivery"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.logs.arn}/documents/*"]
    principals {
      type        = "Service"
      identifiers = ["logging.s3.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.documents.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.logs.arn, "${aws_s3_bucket.logs.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "logs" {
  bucket = aws_s3_bucket.logs.id
  policy = data.aws_iam_policy_document.logs.json
}

resource "aws_s3_bucket_logging" "documents" {
  depends_on    = [aws_s3_bucket_policy.logs]
  bucket        = aws_s3_bucket.documents.id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "documents/"
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.documents.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# Deny any request that is not TLS.
data "aws_iam_policy_document" "tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.documents.arn,
      "${aws_s3_bucket.documents.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "documents" {
  bucket = aws_s3_bucket.documents.id
  policy = data.aws_iam_policy_document.tls_only.json
}

# The service account the application uses: object access to the tenant prefix, nothing else.
# Create its access keys outside Terraform (they would land in state) and put them in AIRA_CLOUD_S3_ACCESS_KEY / _SECRET_KEY.
data "aws_iam_policy_document" "app" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.documents.arn}/tenant/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.documents.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["tenant/*"]
    }
  }
  statement {
    actions   = ["kms:GenerateDataKey", "kms:Encrypt", "kms:Decrypt"]
    resources = [aws_kms_key.documents.arn]
  }
}

resource "aws_iam_policy" "app" {
  name   = "aira-documents-access"
  policy = data.aws_iam_policy_document.app.json
}

output "bucket" {
  value = aws_s3_bucket.documents.bucket
}

output "kms_key_arn" {
  value = aws_kms_key.documents.arn
}

output "app_policy_arn" {
  value       = aws_iam_policy.app.arn
  description = "Attach to the IAM user or role the application runs as."
}

output "environment" {
  description = "Values for the application's environment."
  value = {
    AIRA_CLOUD_STORAGE     = "s3"
    AIRA_CLOUD_S3_BUCKET   = aws_s3_bucket.documents.bucket
    AIRA_CLOUD_S3_REGION   = var.region
    AIRA_CLOUD_S3_ENDPOINT = "https://s3.${var.region}.amazonaws.com"
  }
}
