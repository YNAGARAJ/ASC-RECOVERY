# S3 -- HIPAA-eligible, for inbound 835 files (src/ingestion/sources.py's
# S3PollSource port). Public access blocked at the bucket level AND
# nothing here relies only on that -- see the account-level equivalent
# noted in docs/RUNBOOK.md, since "public-access-blocked at account
# level, not just bucket level" is the Phase 9 prompt's explicit wording
# and an account-level setting isn't something a single bucket's module
# can enforce on its own.

resource "aws_s3_bucket" "remittances" {
  bucket = "${local.name_prefix}-remittances"

  tags = merge(var.tags, {
    Name = "${local.name_prefix}-remittances"
  })
}

resource "aws_s3_bucket_public_access_block" "remittances" {
  bucket = aws_s3_bucket.remittances.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "remittances" {
  bucket = aws_s3_bucket.remittances.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "remittances" {
  bucket = aws_s3_bucket.remittances.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "remittances" {
  bucket = aws_s3_bucket.remittances.id

  rule {
    id     = "retain-six-years"
    status = "Enabled"

    # Applies to every object in the bucket -- an empty filter, not a
    # missing one. The provider currently defaults to this when neither
    # `filter` nor `prefix` is set, but warns that it'll be a hard error
    # in a future version; stating it explicitly now avoids that.
    filter {}

    # HIPAA documentation retention is 6 years -- this transitions
    # (rather than deletes) noncurrent versions to cheaper storage well
    # before that window closes; actual deletion policy is a Phase 11
    # compliance decision, not something this module should decide
    # unilaterally by expiring objects.
    noncurrent_version_transition {
      noncurrent_days = 90
      storage_class   = "GLACIER"
    }
  }
}
