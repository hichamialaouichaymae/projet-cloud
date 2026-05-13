import boto3
import time
from botocore.exceptions import ClientError
from utils.helpers import log_event, load_json

# Configuration des clients vers LocalStack
client_ec2 = boto3.client(
    "ec2",
    endpoint_url="http://localstack:4566",
    region_name="us-east-1",
    aws_access_key_id="test",
    aws_secret_access_key="test"
)

s3_client = boto3.client(
    "s3",
    endpoint_url="http://localstack:4566",
    region_name="us-east-1",
    aws_access_key_id="test",
    aws_secret_access_key="test"
)

# Charger l'état du déploiement (IDs réseau, etc.)
state = load_json("state.json", default={})

def monitor_instances():
    """Surveille et loggue l'état des instances EC2"""
    try:
        resp = client_ec2.describe_instances()
        for r in resp["Reservations"]:
            for i in r["Instances"]:
                log_event(f"Instance {i['InstanceId']} → état {i['State']['Name']}")
    except ClientError as e:
        log_event(f"❌ Erreur monitoring instances : {e}")

def monitor_buckets():
    """Surveille et loggue les buckets S3"""
    try:
        buckets = s3_client.list_buckets()["Buckets"]
        names = [b["Name"] for b in buckets]
        log_event(f"Buckets actifs : {', '.join(names) if names else 'aucun'}")
    except ClientError as e:
        log_event(f"❌ Erreur monitoring buckets : {e}")

def monitor_network():
    """Surveille et loggue les subnets"""
    try:
        subnets = client_ec2.describe_subnets()["Subnets"]
        ids = [s["SubnetId"] for s in subnets]
        log_event(f"Subnets actifs : {', '.join(ids) if ids else 'aucun'}")
    except ClientError as e:
        log_event(f"❌ Erreur monitoring réseau : {e}")

def monitor_all():
    """Surveille toutes les ressources"""
    monitor_instances()
    monitor_buckets()
    monitor_network()

if __name__ == "__main__":
    print("📡 Démarrage du Monitoring en continu")
    while True:
        monitor_all()
        time.sleep(30)  # Vérifie toutes les 30 secondes