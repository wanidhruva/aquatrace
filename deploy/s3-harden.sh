#!/usr/bin/env bash
# AquaTrace S3 evidence-bucket hardening. Run once.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
BUCKET="${AQUATRACE_BUCKET:-aquatrace-evidence-148018683731-ap-south-1}"

aws s3api put-public-access-block --region "$REGION" --bucket "$BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

aws s3api put-bucket-versioning --region "$REGION" --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled

aws s3api put-bucket-encryption --region "$REGION" --bucket "$BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

aws s3api put-bucket-lifecycle-configuration --region "$REGION" --bucket "$BUCKET" \
  --lifecycle-configuration '{
    "Rules":[{
      "ID":"evidence-tiering","Status":"Enabled","Filter":{"Prefix":"evidence/"},
      "Transitions":[
        {"Days":30,"StorageClass":"INTELLIGENT_TIERING"},
        {"Days":90,"StorageClass":"GLACIER"}],
      "Expiration":{"Days":730},
      "NoncurrentVersionExpiration":{"NoncurrentDays":90}
    }]}'

echo "hardened s3://$BUCKET"
