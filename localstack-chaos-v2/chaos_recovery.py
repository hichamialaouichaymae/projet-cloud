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

# Charger la config des pannes et l'état du déploiement
chaos_config = load_json("chaos_config.json", default={"failures": []})
state = load_json("state.json", default={})

def log_instance_states():
    """Loggue l'état de toutes les instances EC2"""
    try:
        resp = client_ec2.describe_instances()
        for r in resp["Reservations"]:
            for i in r["Instances"]:
                log_event(f"Instance {i['InstanceId']} → état {i['State']['Name']}")
    except ClientError as e:
        log_event(f"❌ Erreur récupération états instances : {e}")

def recover_instance():
    """Recrée une instance EC2 si aucune n'est active"""
    try:
        resp = client_ec2.describe_instances()
        active = [i for r in resp["Reservations"] for i in r["Instances"] if i["State"]["Name"] == "running"]
        if not active:
            resp = client_ec2.run_instances(ImageId="ami-test", MinCount=1, MaxCount=1)
            vm = resp["Instances"][0]["InstanceId"]
            log_event(f"🔄 Nouvelle instance {vm} recréée")
    except ClientError as e:
        log_event(f"❌ Erreur récupération instance : {e}")

def recover_bucket(bucket="demo-bucket-chaos-v2"):
    """Recrée le bucket S3 si supprimé"""
    try:
        buckets = s3_client.list_buckets()["Buckets"]
        names = [b["Name"] for b in buckets]
        if bucket not in names:
            s3_client.create_bucket(Bucket=bucket)
            log_event(f"🔄 Bucket {bucket} recréé")
    except ClientError as e:
        log_event(f"❌ Erreur récupération bucket : {e}")

def recover_network():
    """Recrée un subnet si supprimé (IDs depuis state.json)"""
    try:
        subnet_id = state.get("subnet_id", "subnet-123456")
        vpc_id = state.get("vpc_id", "vpc-123456")
        subnets = client_ec2.describe_subnets()["Subnets"]
        ids = [s["SubnetId"] for s in subnets]
        if subnet_id not in ids:
            client_ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.1.0/24")
            log_event(f"🔄 Subnet {subnet_id} recréé")
    except ClientError as e:
        log_event(f"❌ Erreur récupération réseau : {e}")

def recover_cpu_stress():
    """Arrête les instances taggées chaos=cpu-stress et recrée une instance normale"""
    try:
        resp = client_ec2.describe_instances()
        stress_vms = [
            i["InstanceId"]
            for r in resp["Reservations"]
            for i in r["Instances"]
            if any(t["Key"] == "chaos" and t["Value"] == "cpu-stress" for t in i.get("Tags", []))
        ]
        if stress_vms:
            client_ec2.terminate_instances(InstanceIds=stress_vms)
            log_event(f"🔄 Instances CPU stress {stress_vms} arrêtées")
            # Recréer une instance normale pour compenser
            resp = client_ec2.run_instances(ImageId="ami-test", MinCount=1, MaxCount=1)
            vm = resp["Instances"][0]["InstanceId"]
            log_event(f"🔄 Nouvelle instance {vm} recréée après CPU stress")
    except ClientError as e:
        log_event(f"❌ Erreur récupération CPU stress : {e}")

def recover_all():
    """Vérifie et récupère toutes les ressources"""
    log_instance_states()
    recover_instance()
    recover_bucket()
    recover_network()
    recover_cpu_stress()

if __name__ == "__main__":
    print("🚀 Démarrage du Chaos Recovery Monitor")
    while True:
        recover_all()
        time.sleep(30)