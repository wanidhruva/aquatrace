#!/usr/bin/env bash
# AquaTrace CloudWatch alarms (pilot set). Run once per environment.
# Prerequisites: AWS CLI with rights to put alarms; CloudWatch agent on the
# host for the disk_used_percent metric (standard CWAgent config).
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
NS="AquaTrace"
# Space-separated camera ids from backend/cameras.yaml (registry).
CAMERAS="${AQUATRACE_CAMERAS:-cam-1 cam-2 cam-3 cam-4}"
# SNS topic for alarm notifications (pages the operator).
ALARM_TOPIC="${AQUATRACE_ALARM_TOPIC:-arn:aws:sns:ap-south-1:148018683731:aquatrace-critical-alerts}"

alarm() { # name, metric, dims(json), stat, threshold, op, period, evals, missing
  aws cloudwatch put-metric-alarm --region "$REGION" \
    --alarm-name "$1" --namespace "$NS" --metric-name "$2" \
    --dimensions "$3" --statistic "$4" --threshold "$5" \
    --comparison-operator "$6" --period "$7" --evaluation-periods "$8" \
    --treat-missing-data "$9" --alarm-actions "$ALARM_TOPIC" --ok-actions "$ALARM_TOPIC"
}

# 1. A camera went silent (no telemetry for 5 min) -> page. This is the
#    3am-camera-death alarm; missing data BREACHES by design.
for cam in $CAMERAS; do
  alarm "aquatrace-telemetry-missing-$cam" "WaterAreaRatio" \
    "[{\"Name\":\"CameraId\",\"Value\":\"$cam\"}]" \
    "SampleCount" 1 "LessThanThreshold" 300 1 "breaching"
done

# 2. AWS publish failures (outbox is retrying but something is wrong).
alarm "aquatrace-publish-failures" "AwsPublishFailures" \
  "[{\"Name\":\"CameraId\",\"Value\":\"outbox\"}]" \
  "Sum" 1 "GreaterThanOrEqualToThreshold" 300 1 "notBreaching"

# 3. Disk pressure on the host (CloudWatch agent metric).
aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name "aquatrace-disk-pressure" --namespace "CWAgent" \
  --metric-name "disk_used_percent" \
  --dimensions '[{"Name":"host","Value":"aquatrace-pilot"}]' \
  --statistic "Average" --threshold 85 --comparison-operator "GreaterThanThreshold" \
  --period 300 --evaluation-periods 2 --treat-missing-data "notBreaching" \
  --alarm-actions "$ALARM_TOPIC"

echo "alarms created in $REGION"
