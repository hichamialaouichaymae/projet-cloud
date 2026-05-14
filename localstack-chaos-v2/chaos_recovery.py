"""
chaos_recovery.py  —  version corrigée
Surveille et recrée automatiquement les ressources supprimées.
Boucle toutes les 3 secondes.

CORRECTIONS :
  - Suppression de log_instance_states() → évite le double-log avec monitoring.py
  - recover_instance() : terminate = vraie panne, recrée une nouvelle instance
  - recover_network() : réutilise un subnet existant OU crée avec CIDR libre
                        (plus d'InvalidSubnet.Conflict)
"""

import os
import json
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

STATE_FILE  = os.getenv("STATE_FILE",    "state.json")
BUCKET_NAME = os.getenv("BUCKET_NAME",  "demo-bucket-chaos-v2")
AMI_ID      = os.getenv("AMI_ID",       "ami-test")
ITYPE       = os.getenv("INSTANCE_TYPE","t3.micro")

def _load_state():
    return load_json(STATE_FILE, default={})

def _save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def _active_instances(ec2):
    """Retourne les instances qui ne sont PAS terminated ni shutting-down."""
    resp = ec2.describe_instances()
    return [
        i
        for r in resp.get("Reservations", [])
        for i in r.get("Instances", [])
        if i["State"]["Name"] not in ("terminated", "shutting-down")
    ]

def _free_cidr_for_vpc(ec2, vpc_id: str) -> str:
    """Retourne un CIDR /24 libre (pas de conflit) dans le VPC donné."""
    used = set()
    for s in ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]).get("Subnets", []):
        used.add(s["CidrBlock"])
    for x in range(256):
        candidate = f"10.0.{x}.0/24"
        if candidate not in used:
            return candidate
    return "10.1.0.0/24"

def _get_valid_subnet(ec2, preferred_id=None):
    """
    Retourne un subnet_id valide.
    1. Préféré encore présent → le retourner.
    2. Autre subnet dans le même VPC → le réutiliser + màj state.
    3. N'importe quel subnet → le réutiliser + màj state.
    4. Aucun subnet → créer avec un CIDR LIBRE.
    """
    try:
        state       = _load_state()
        vpc_id      = state.get("vpc_id")
        all_subnets = ec2.describe_subnets().get("Subnets", [])
        existing_ids = {s["SubnetId"] for s in all_subnets}

        if preferred_id and preferred_id in existing_ids:
            return preferred_id

        if vpc_id:
            same_vpc = [s for s in all_subnets if s["VpcId"] == vpc_id]
            if same_vpc:
                chosen = same_vpc[0]["SubnetId"]
                log_event(f"⚠️ Subnet {preferred_id} absent → réutilisation {chosen}")
                state["subnet_id"] = chosen
                _save_state(state)
                return chosen

        if all_subnets:
            chosen = all_subnets[0]["SubnetId"]
            log_event(f"⚠️ Subnet {preferred_id} absent → réutilisation {chosen}")
            state["subnet_id"] = chosen
            _save_state(state)
            return chosen

        # Aucun subnet → en créer un avec CIDR libre
        if not vpc_id:
            vpcs = ec2.describe_vpcs().get("Vpcs", [])
            if not vpcs:
                log_event("⚠️ Aucun VPC disponible")
                return None
            vpc_id = vpcs[0]["VpcId"]
            state["vpc_id"] = vpc_id

        cidr   = _free_cidr_for_vpc(ec2, vpc_id)
        result = ec2.create_subnet(VpcId=vpc_id, CidrBlock=cidr)
        new_sid = result["Subnet"]["SubnetId"]
        log_event(f"🔄 Subnet {new_sid} ({cidr}) recréé dans VPC {vpc_id}")
        state["subnet_id"] = new_sid
        _save_state(state)
        return new_sid

    except ClientError as e:
        log_event(f"❌ _get_valid_subnet : {e}")
        return None

def _get_valid_sg(ec2, preferred_id=None):
    if not preferred_id:
        return None
    try:
        ec2.describe_security_groups(GroupIds=[preferred_id])
        return preferred_id
    except ClientError:
        log_event(f"⚠️ SG {preferred_id} introuvable → lancement sans SG")
        return None


# ─────────────────────────────────────────────────────────────────────────────
def recover_instance():
    """
    - running existe  → rien à faire
    - aucune instance active (toutes terminated) → créer une nouvelle
    """
    ec2 = _ec2()
    try:
        instances = _active_instances(ec2)
        running   = [i for i in instances if i["State"]["Name"] == "running"]

        if running:
            return  # tout va bien

        # Toutes terminées (ou aucune) → recréer
        state     = _load_state()
        subnet_id = _get_valid_subnet(ec2, state.get("subnet_id"))
        sg_id     = _get_valid_sg(ec2, state.get("sg_id"))

        kwargs = {}
        if subnet_id:
            net = {"DeviceIndex": 0, "SubnetId": subnet_id,
                   "AssociatePublicIpAddress": True}
            if sg_id:
                net["Groups"] = [sg_id]
            kwargs["NetworkInterfaces"] = [net]

        resp = ec2.run_instances(
            ImageId=AMI_ID, MinCount=1, MaxCount=1, InstanceType=ITYPE,
            TagSpecifications=[{"ResourceType": "instance",
                                "Tags": [{"Key": "chaos", "Value": "true"}]}],
            **kwargs
        )
        vm = resp["Instances"][0]["InstanceId"]
        state["instance_id"] = vm
        _save_state(state)
        log_event(f"🔄 Nouvelle instance {vm} créée en remplacement (recovery)")

    except ClientError as e:
        log_event(f"❌ Erreur récupération instance : {e}")


def recover_bucket():
    s3 = _s3()
    try:
        names = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
        if BUCKET_NAME not in names:
            s3.create_bucket(Bucket=BUCKET_NAME)
            log_event(f"🔄 Bucket {BUCKET_NAME} recréé (recovery)")
    except ClientError as e:
        log_event(f"❌ Erreur récupération bucket : {e}")


def recover_network():
    ec2   = _ec2()
    state = _load_state()
    target = state.get("subnet_id")
    if not target:
        return
    try:
        existing = {s["SubnetId"] for s in ec2.describe_subnets().get("Subnets", [])}
        if target not in existing:
            new_sid = _get_valid_subnet(ec2, target)
            if new_sid and new_sid != target:
                log_event(f"🔄 Réseau recovery : subnet actif → {new_sid}")
    except ClientError as e:
        log_event(f"❌ Erreur récupération réseau : {e}")


def recover_cpu_stress():
    ec2 = _ec2()
    try:
        stress = [
            i["InstanceId"] for i in _active_instances(ec2)
            if any(t["Key"] == "chaos" and t["Value"] == "cpu-stress"
                   for t in i.get("Tags", []))
            and i["State"]["Name"] == "running"
        ]
        if stress:
            ec2.terminate_instances(InstanceIds=stress)
            log_event(f"🔄 Instances CPU-stress {stress} terminées (recovery)")
    except ClientError as e:
        log_event(f"❌ Erreur récupération CPU stress : {e}")


def recover_all():
    recover_instance()
    recover_bucket()
    recover_network()
    recover_cpu_stress()


if __name__ == "__main__":
    print("🚀 Démarrage du Chaos Recovery Monitor (3s)")
    while True:
        recover_all()
        time.sleep(3)