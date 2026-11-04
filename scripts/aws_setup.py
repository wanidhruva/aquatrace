#!/usr/bin/env python3
"""Provision AquaTrace's AWS backing services (idempotent).

Creates:
  - S3 bucket   aquatrace-evidence-<account>-<region>   (evidence vault)
  - SNS topic   aquatrace-critical-alerts               (critical delivery)

Then writes backend/data/aws.env with the exports the backend reads, and
prints them for the shell.

    python3 scripts/aws_setup.py
    python3 scripts/aws_setup.py --subscribe-email you@example.com
    python3 scripts/aws_setup.py --profile divesh --region ap-south-1

Costs are pennies for a demo (S3 storage of JPEG evidence, a handful of SNS
publishes, a few CloudWatch custom metrics).
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(ROOT, "backend", "data", "aws.env")


def session(profile: str, region: str):
    import boto3
    from botocore.config import Config
    cfg = Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2})
    sess = boto3.Session(profile_name=profile or None, region_name=region)
    return sess, cfg


def ensure_bucket(s3, bucket: str, region: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"  S3 bucket s3://{bucket} already exists")
        return
    except Exception:
        pass
    kwargs: dict = {"Bucket": bucket}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**kwargs)
    print(f"  S3 bucket s3://{bucket} created")


def ensure_topic(sns, name: str) -> str:
    arn = sns.create_topic(Name=name)["TopicArn"]
    print(f"  SNS topic {arn}")
    return arn


def main() -> int:
    ap = argparse.ArgumentParser(description="Provision AquaTrace AWS resources")
    ap.add_argument("--profile", default=os.environ.get("AQUATRACE_AWS_PROFILE", "divesh"))
    ap.add_argument("--region", default=os.environ.get("AQUATRACE_AWS_REGION", "ap-south-1"))
    ap.add_argument("--subscribe-email", default="",
                    help="email to subscribe to critical alerts (confirms via inbox)")
    args = ap.parse_args()

    sess, cfg = session(args.profile, args.region)
    sts = sess.client("sts", config=cfg)
    account = sts.get_caller_identity()["Account"]
    print(f"  account {account} region {args.region} profile {args.profile or '(default)'}")

    s3 = sess.client("s3", config=cfg)
    bucket = f"aquatrace-evidence-{account}-{args.region}"
    ensure_bucket(s3, bucket, args.region)

    sns = sess.client("sns", config=cfg)
    topic_arn = ensure_topic(sns, "aquatrace-critical-alerts")

    if args.subscribe_email:
        sns.subscribe(TopicArn=topic_arn, Protocol="email",
                      Endpoint=args.subscribe_email)
        print(f"  subscribed {args.subscribe_email} (check inbox to confirm)")

    os.makedirs(os.path.dirname(ENV_FILE), exist_ok=True)
    lines = [
        "AQUATRACE_AWS_ENABLED=1",
        f"AQUATRACE_AWS_PROFILE={args.profile}",
        f"AQUATRACE_AWS_REGION={args.region}",
        f"AQUATRACE_S3_BUCKET={bucket}",
        f"AQUATRACE_SNS_TOPIC_ARN={topic_arn}",
    ]
    with open(ENV_FILE, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"  wrote {ENV_FILE}")

    print("\nAdd to your shell (or rely on scripts/start.py, which loads aws.env):")
    for line in lines:
        print(f"  export {line}")
    print("\nVerify:  curl localhost:8000/aws/status")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
