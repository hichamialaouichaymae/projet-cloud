"""
monitoring.py
Surveille et loggue l'état de toutes les ressources AWS LocalStack.
"""

import os
import time
import boto3
from botocore.exceptions import ClientError
from utils.helpers import log_event, load_json

AWS_ENDPOINT = os.getenv("AWS_ENDPOINT_URL",      "http://localstack:4566")
AWS_REGION   = os.getenv("AWS_DEFAULT_REGION",    "us-east-1")
AWS_ACCESS   = os.getenv("AWS_ACCESS_KEY_ID",     "test")
AWS_SECRET   = os.getenv("AWS_SECRET_ACCESS_KEY", "test")

def _ec2():
    return boto3.client("ec2",
        endpoint_url=AWS_ENDPOINT, region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS, aws_secret_access_key=AWS_SECRET)

def _s3():
    return boto3.client("s3",
        endpoint_url=AWS_ENDPOINT, region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS, aws_secret_access_key=AWS_SECRET)

def monitor_instances():
    try:
        resp = _ec2().describe_instances()
        for r in resp.get("Reservations", []):
            for i in r.get("Instances", []):
                log_event(f"Instance {i['InstanceId']} → état {i['State']['Name']}")
    except ClientError as e:
        log_event(f"❌ Erreur monitoring instances : {e}")

def monitor_buckets():
    try:
        buckets = _s3().list_buckets().get("Buckets", [])
        names = [b["Name"] for b in buckets]
        log_event(f"Buckets actifs : {', '.join(names) if names else 'aucun'}")
    except ClientError as e:
        log_event(f"❌ Erreur monitoring buckets : {e}")

def monitor_network():
    try:
        subnets = _ec2().describe_subnets().get("Subnets", [])
        ids = [s["SubnetId"] for s in subnets]
        log_event(f"Subnets actifs : {', '.join(ids) if ids else 'aucun'}")
    except ClientError as e:
        log_event(f"❌ Erreur monitoring réseau : {e}")

def monitor_all():
    monitor_instances()
    monitor_buckets()
    monitor_network()

if __name__ == "__main__":
    print("📡 Démarrage du Monitoring en continu")
    while True:
        monitor_all()
        time.sleep(30)
