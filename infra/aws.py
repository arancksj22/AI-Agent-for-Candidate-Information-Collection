"""Minimal AWS setup for replaying failed writes: SQS queue → Lambda, with an SQS DLQ behind it.

  python infra/aws.py deploy     create/update everything, print the SQS_QUEUE_URL for .env
  python infra/aws.py status     show queue depths
  python infra/aws.py teardown   delete everything

Uses your default AWS credentials/region (aws configure). Idempotent: safe to run deploy twice.
"""
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

NAME = "candidate-writes"
DLQ = "candidate-writes-dlq"
FUNC = "candidate-write-replayer"
ROLE = "candidate-write-replayer-role"
REGION = os.environ.get("AWS_REGION") or boto3.session.Session().region_name or "us-east-1"

sqs = boto3.client("sqs", region_name=REGION)
lam = boto3.client("lambda", region_name=REGION)
iam = boto3.client("iam")


def deploy():
    dlq_url = sqs.create_queue(QueueName=DLQ)["QueueUrl"]
    dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    q_url = sqs.create_queue(QueueName=NAME, Attributes={
        "VisibilityTimeout": "60",
        "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "3"}),
    })["QueueUrl"]
    q_arn = sqs.get_queue_attributes(QueueUrl=q_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    print(f"queues: {NAME} → (after 3 failed receives) {DLQ}")

    try:
        role_arn = iam.get_role(RoleName=ROLE)["Role"]["Arn"]
    except ClientError:
        role_arn = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                           "Action": "sts:AssumeRole"}]}))["Role"]["Arn"]
        iam.attach_role_policy(RoleName=ROLE,
                               PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole")
        time.sleep(10)                                     # IAM propagation
    print(f"role: {ROLE}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(ROOT / "infra" / "lambda_replayer" / "handler.py", "handler.py")
    env = {"Variables": {"CANDIDATE_API_BASE_URL": os.environ["CANDIDATE_API_BASE_URL"],
                         "CANDIDATE_API_KEY": os.environ["CANDIDATE_API_KEY"]}}
    try:
        lam.update_function_code(FunctionName=FUNC, ZipFile=buf.getvalue())
        lam.get_waiter("function_updated").wait(FunctionName=FUNC)
        lam.update_function_configuration(FunctionName=FUNC, Environment=env)
    except lam.exceptions.ResourceNotFoundException:
        for attempt in range(5):
            try:
                lam.create_function(FunctionName=FUNC, Runtime="python3.12", Role=role_arn,
                                    Handler="handler.handler", Code={"ZipFile": buf.getvalue()},
                                    Timeout=30, Environment=env)
                break
            except lam.exceptions.InvalidParameterValueException:
                time.sleep(5)                              # role not assumable yet
    lam.get_waiter("function_active").wait(FunctionName=FUNC)
    print(f"lambda: {FUNC}")

    if not lam.list_event_source_mappings(FunctionName=FUNC, EventSourceArn=q_arn)["EventSourceMappings"]:
        lam.create_event_source_mapping(FunctionName=FUNC, EventSourceArn=q_arn, BatchSize=1)
    print("trigger: SQS → Lambda (batch size 1)")
    print(f"\nAdd to .env:\nSQS_QUEUE_URL={q_url}\nAWS_REGION={REGION}")


def status():
    for name in (NAME, DLQ):
        try:
            url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
        except ClientError:
            print(f"{name}: not deployed")
            continue
        a = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=[
            "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        print(f"{name}: {a['ApproximateNumberOfMessages']} waiting, "
              f"{a['ApproximateNumberOfMessagesNotVisible']} in flight")


def teardown():
    for m in lam.list_event_source_mappings(FunctionName=FUNC).get("EventSourceMappings", []) \
            if _exists(lambda: lam.get_function(FunctionName=FUNC)) else []:
        lam.delete_event_source_mapping(UUID=m["UUID"])
    _quiet(lambda: lam.delete_function(FunctionName=FUNC))
    for name in (NAME, DLQ):
        _quiet(lambda: sqs.delete_queue(QueueUrl=sqs.get_queue_url(QueueName=name)["QueueUrl"]))
    _quiet(lambda: iam.detach_role_policy(
        RoleName=ROLE, PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole"))
    _quiet(lambda: iam.delete_role(RoleName=ROLE))
    print("deleted: trigger, lambda, queues, role")


def _exists(fn):
    try:
        fn()
        return True
    except ClientError:
        return False


def _quiet(fn):
    try:
        fn()
    except ClientError as e:
        print(f"  (skip: {e.response['Error']['Code']})")


if __name__ == "__main__":
    {"deploy": deploy, "status": status, "teardown": teardown}[sys.argv[1] if len(sys.argv) > 1 else "status"]()
