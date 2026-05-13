"""
chaos_engine.py
Moteur d'injection de pannes réelles sur LocalStack.
Chaque fonction correspond à un failure_id de chaos_config.json.
"""

import os
import random
import boto3
from botocore.exceptions import ClientError
from utils.helpers import log_event, load_json

# ─── Clients AWS → LocalStack ───────────────────────────────────────────────
AWS_ENDPOINT  = os.getenv("AWS_ENDPOINT_URL",        "http://localstack:4566")
AWS_REGION    = os.getenv("AWS_DEFAULT_REGION",       "us-east-1")
AWS_ACCESS    = os.getenv("AWS_ACCESS_KEY_ID",        "test")
AWS_SECRET    = os.getenv("AWS_SECRET_ACCESS_KEY",    "test")

def _ec2():
    return boto3.client("ec2",
        endpoint_url=AWS_ENDPOINT, region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS, aws_secret_access_key=AWS_SECRET)

def _s3():
    return boto3.client("s3",
        endpoint_url=AWS_ENDPOINT, region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS, aws_secret_access_key=AWS_SECRET)

# ─── Helpers internes ───────────────────────────────────────────────────────
def _all_instances_not_terminated(ec2):
    """Retourne toutes les instances non-terminées."""
    resp = ec2.describe_instances()
    return [
        i
        for r in resp.get("Reservations", [])
        for i in r.get("Instances", [])
        if i["State"]["Name"] not in ("terminated",)
    ]

def _running_instances(ec2):
    return [i for i in _all_instances_not_terminated(ec2) if i["State"]["Name"] == "running"]

def _state_path():
    return os.getenv("STATE_FILE", "state.json")

# ════════════════════════════════════════════════════════════════
#  PANNES
# ════════════════════════════════════════════════════════════════

def kill_instance() -> dict:
    """Arrête (stop) une instance EC2 running au hasard."""
    ec2 = _ec2()
    running = _running_instances(ec2)
    if not running:
        msg = "⚠️ Aucune instance running à arrêter"
        log_event(msg)
        return {"status": "warning", "message": msg}

    victim = random.choice(running)
    iid = victim["InstanceId"]
    try:
        ec2.stop_instances(InstanceIds=[iid])
        msg = f"💥 [CHAOS] Instance {iid} arrêtée (kill_instance)"
        log_event(msg)
        return {"status": "success", "message": msg, "instance_id": iid}
    except ClientError as e:
        msg = f"❌ kill_instance échoué : {e}"
        log_event(msg)
        return {"status": "error", "message": msg}


def delete_bucket() -> dict:
    """Supprime le bucket S3 principal (demo-bucket-chaos-v2)."""
    s3  = _s3()
    state = load_json(_state_path(), default={})
    bucket = state.get("bucket", "demo-bucket-chaos-v2")
    try:
        # Vider d'abord le bucket
        objs = s3.list_objects_v2(Bucket=bucket).get("Contents", [])
        if objs:
            s3.delete_objects(Bucket=bucket,
                Delete={"Objects": [{"Key": o["Key"]} for o in objs]})
        s3.delete_bucket(Bucket=bucket)
        msg = f"💥 [CHAOS] Bucket {bucket} supprimé (delete_bucket)"
        log_event(msg)
        return {"status": "success", "message": msg, "bucket": bucket}
    except ClientError as e:
        if "NoSuchBucket" in str(e):
            msg = f"⚠️ Bucket {bucket} introuvable (déjà supprimé ?)"
            log_event(msg)
            return {"status": "warning", "message": msg}
        msg = f"❌ delete_bucket échoué : {e}"
        log_event(msg)
        return {"status": "error", "message": msg}


def network_failure() -> dict:
    """Supprime un subnet pour simuler une panne réseau."""
    ec2 = _ec2()
    state = load_json(_state_path(), default={})
    target_subnet = state.get("subnet_id")

    try:
        subnets = ec2.describe_subnets().get("Subnets", [])
        if not subnets:
            msg = "⚠️ Aucun subnet disponible à supprimer"
            log_event(msg)
            return {"status": "warning", "message": msg}

        # Priorité au subnet enregistré dans state.json
        subnet = next((s for s in subnets if s["SubnetId"] == target_subnet), None)
        if subnet is None:
            subnet = random.choice(subnets)

        sid = subnet["SubnetId"]
        ec2.delete_subnet(SubnetId=sid)
        msg = f"💥 [CHAOS] Subnet {sid} supprimé (network_failure)"
        log_event(msg)
        return {"status": "success", "message": msg, "subnet_id": sid}
    except ClientError as e:
        msg = f"❌ network_failure échoué : {e}"
        log_event(msg)
        return {"status": "error", "message": msg}


def cpu_stress() -> dict:
    """Lance une instance EC2 avec le tag chaos=cpu-stress pour simuler une surcharge CPU."""
    ec2 = _ec2()
    state = load_json(_state_path(), default={})
    ami   = os.getenv("AMI_ID", "ami-test")
    itype = os.getenv("INSTANCE_TYPE", "t3.micro")

    # Paramètres réseau optionnels depuis state
    net_args = {}
    if state.get("subnet_id") and state.get("sg_id"):
        net_args["NetworkInterfaces"] = [{
            "DeviceIndex": 0,
            "SubnetId":  state["subnet_id"],
            "Groups":    [state["sg_id"]],
            "AssociatePublicIpAddress": True,
        }]

    try:
        resp = ec2.run_instances(
            ImageId=ami, InstanceType=itype,
            MinCount=1, MaxCount=1,
            TagSpecifications=[{
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "chaos",  "Value": "cpu-stress"},
                    {"Key": "status", "Value": "stressed"},
                ]
            }],
            **net_args
        )
        iid = resp["Instances"][0]["InstanceId"]
        msg = f"💥 [CHAOS] Instance {iid} lancée avec CPU-stress (cpu_stress)"
        log_event(msg)
        return {"status": "success", "message": msg, "instance_id": iid}
    except ClientError as e:
        msg = f"❌ cpu_stress échoué : {e}"
        log_event(msg)
        return {"status": "error", "message": msg}


def multi_failure() -> dict:
    """Combine kill_instance + delete_bucket + network_failure en une seule opération."""
    results = []
    for fn in (kill_instance, delete_bucket, network_failure):
        results.append(fn())
    msg = "💥 [CHAOS] Multi-failure : kill_instance + delete_bucket + network_failure déclenchés"
    log_event(msg)
    return {"status": "success", "message": msg, "details": results}


# ─── Registre des pannes ─────────────────────────────────────────────────────
FAILURE_REGISTRY = {
    "kill_instance":   kill_instance,
    "delete_bucket":   delete_bucket,
    "network_failure": network_failure,
    "cpu_stress":      cpu_stress,
    "multi_failure":   multi_failure,
}


def execute_failure(failure_id: str) -> dict:
    """Point d'entrée principal — appelé depuis app.py."""
    fn = FAILURE_REGISTRY.get(failure_id)
    if fn is None:
        return {"status": "error", "message": f"failure_id inconnu : {failure_id}"}
    try:
        return fn()
    except Exception as e:
        msg = f"❌ Erreur inattendue dans {failure_id} : {e}"
        log_event(msg)
        return {"status": "error", "message": msg}
