"""
chaos_recovery.py
Surveille l'état de l'infrastructure LocalStack et recrée
automatiquement les ressources supprimées par une injection de panne.
Tourne en boucle indépendante toutes les 10 secondes.
"""
 
import os
import time
import boto3
from botocore.exceptions import ClientError
from utils.helpers import log_event, load_json
 
# ─── Chemin absolu pour les logs (évite divergence avec Flask) ───────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH  = os.path.join(BASE_DIR, "logs", "events.log")
 
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
 
STATE_FILE  = os.getenv("STATE_FILE",      os.path.join(BASE_DIR, "state.json"))
BUCKET_NAME = os.getenv("BUCKET_NAME",     "demo-bucket-chaos-v2")
AMI_ID      = os.getenv("AMI_ID",          "ami-test")
ITYPE       = os.getenv("INSTANCE_TYPE",   "t3.micro")
 
def _load_state():
    return load_json(STATE_FILE, default={})
 
def _all_instances(ec2):
    resp = ec2.describe_instances()
    return [
        i
        for r in resp.get("Reservations", [])
        for i in r.get("Instances", [])
        if i["State"]["Name"] not in ("terminated",)
    ]
 
# ─────────────────────────────────────────────────────────────────────────────
def _get_valid_subnet(ec2, preferred_id=None):
    try:
        subnets = ec2.describe_subnets().get("Subnets", [])
        existing_ids = [s["SubnetId"] for s in subnets]
 
        if preferred_id and preferred_id in existing_ids:
            return preferred_id
 
        if subnets:
            chosen = subnets[0]["SubnetId"]
            if preferred_id:
                log_event(f"⚠️ Subnet {preferred_id} introuvable → utilisation de {chosen}", LOG_PATH)
            return chosen
 
        vpcs = ec2.describe_vpcs().get("Vpcs", [])
        if not vpcs:
            log_event("⚠️ Aucun VPC disponible, impossible de recréer un subnet", LOG_PATH)
            return None
 
        vpc_id = vpcs[0]["VpcId"]
        result = ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.1.0/24")
        new_sid = result["Subnet"]["SubnetId"]
        log_event(f"🔄 Subnet {new_sid} recréé dans VPC {vpc_id} (recovery)", LOG_PATH)
        return new_sid
 
    except ClientError as e:
        log_event(f"❌ _get_valid_subnet : {e}", LOG_PATH)
        return None
 
 
def _get_valid_sg(ec2, preferred_id=None):
    if not preferred_id:
        return None
    try:
        ec2.describe_security_groups(GroupIds=[preferred_id])
        return preferred_id
    except ClientError:
        log_event(f"⚠️ Security Group {preferred_id} introuvable → lancement sans SG", LOG_PATH)
        return None
 
 
def log_instance_states():
    try:
        for i in _all_instances(_ec2()):
            log_event(f"Instance {i['InstanceId']} → état {i['State']['Name']}", LOG_PATH)
    except ClientError as e:
        log_event(f"❌ Erreur récupération états instances : {e}", LOG_PATH)
 
 
def recover_instance():
    """
    Stratégie de recovery en deux temps :
      1. Si des instances sont stopped → les redémarrer (start_instances).
      2. S'il n'y a vraiment aucune instance → en créer une nouvelle.
    """
    ec2 = _ec2()
    try:
        instances = _all_instances(ec2)
        active = [i for i in instances if i["State"]["Name"] == "running"]
        if active:
            return  # Tout va bien
 
        # FIX BUG 1 : redémarrer les stopped au lieu de recréer à l'infini
        stopped_ids = [i["InstanceId"] for i in instances if i["State"]["Name"] == "stopped"]
        if stopped_ids:
            ec2.start_instances(InstanceIds=stopped_ids)
            log_event(f"🔄 Instance(s) {stopped_ids} redémarrée(s) (recovery)", LOG_PATH)
            return
 
        # Aucune instance du tout → créer une nouvelle
        state     = _load_state()
        subnet_id = _get_valid_subnet(ec2, state.get("subnet_id"))
        sg_id     = _get_valid_sg(ec2, state.get("sg_id"))
 
        kwargs = {}
        if subnet_id:
            net = {
                "DeviceIndex": 0,
                "SubnetId":    subnet_id,
                "AssociatePublicIpAddress": True,
            }
            if sg_id:
                net["Groups"] = [sg_id]
            kwargs["NetworkInterfaces"] = [net]
 
        resp = ec2.run_instances(
            ImageId=AMI_ID, MinCount=1, MaxCount=1,
            InstanceType=ITYPE, **kwargs
        )
        vm = resp["Instances"][0]["InstanceId"]
        log_event(f"🔄 Nouvelle instance {vm} recréée (recovery)", LOG_PATH)
 
    except ClientError as e:
        log_event(f"❌ Erreur récupération instance : {e}", LOG_PATH)
 
 
def recover_bucket():
    s3 = _s3()
    try:
        names = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
        if BUCKET_NAME not in names:
            s3.create_bucket(Bucket=BUCKET_NAME)
            log_event(f"🔄 Bucket {BUCKET_NAME} recréé (recovery)", LOG_PATH)
    except ClientError as e:
        log_event(f"❌ Erreur récupération bucket : {e}", LOG_PATH)
 
 
def recover_network():
    ec2    = _ec2()
    state  = _load_state()
    target = state.get("subnet_id")
    if not target:
        return
 
    try:
        existing = [s["SubnetId"] for s in ec2.describe_subnets().get("Subnets", [])]
        if target not in existing:
            vpc_id = state.get("vpc_id")
            if vpc_id:
                try:
                    ec2.describe_vpcs(VpcIds=[vpc_id])
                except ClientError:
                    vpc_id = None
 
            if not vpc_id:
                vpcs = ec2.describe_vpcs().get("Vpcs", [])
                if not vpcs:
                    log_event("⚠️ Aucun VPC disponible pour recréer le subnet", LOG_PATH)
                    return
                vpc_id = vpcs[0]["VpcId"]
 
            result = ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.1.0/24")
            new_sid = result["Subnet"]["SubnetId"]
            log_event(f"🔄 Subnet {new_sid} recréé dans VPC {vpc_id} (recovery)", LOG_PATH)
 
    except ClientError as e:
        log_event(f"❌ Erreur récupération réseau : {e}", LOG_PATH)
 
 
def recover_cpu_stress():
    ec2 = _ec2()
    try:
        instances = _all_instances(ec2)
        stress = [
            i["InstanceId"] for i in instances
            if any(t["Key"] == "chaos" and t["Value"] == "cpu-stress"
                   for t in i.get("Tags", []))
            and i["State"]["Name"] == "running"
        ]
        if stress:
            ec2.terminate_instances(InstanceIds=stress)
            log_event(f"🔄 Instances CPU-stress {stress} terminées (recovery)", LOG_PATH)
    except ClientError as e:
        log_event(f"❌ Erreur récupération CPU stress : {e}", LOG_PATH)
 
 
def recover_all():
    log_instance_states()
    recover_instance()
    recover_bucket()
    recover_network()
    recover_cpu_stress()
    # Toujours émettre un log de fin de cycle → permet au bandeau frontend de se fermer
    log_event("🔄 [RECOVERY] Cycle de vérification terminé", LOG_PATH)
 
 
if __name__ == "__main__":
    print("🚀 Démarrage du Chaos Recovery Monitor")
    recover_all()   # Premier cycle immédiat au démarrage
    while True:
        time.sleep(10)   # FIX : 10s au lieu de 30s
        recover_all()