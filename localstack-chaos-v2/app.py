import os
import json
import time
import threading
import subprocess
from flask import Flask, jsonify, render_template, Response, stream_with_context
import boto3
from prometheus_flask_exporter import PrometheusMetrics
from utils.helpers import load_json, log_event
import monitoring
import chaos_engine

# ─── Flask ────────────────────────────────────────────────────────────────────
app = Flask(__name__)
metrics = PrometheusMetrics(app)
metrics.info("flask_app_info", "Chaos Control Center v2", version="2.0.0")

# ─── Config AWS ───────────────────────────────────────────────────────────────
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

# ─── Chaos config ─────────────────────────────────────────────────────────────
_chaos_path = "config/chaos_config.json" if os.path.exists("config/chaos_config.json") else "chaos_config.json"
chaos_config = load_json(_chaos_path, default={"failures": []})

LOG_PATH  = "logs/events.log"
_log_lock = threading.Lock()

# ══════════════════════════════════════════════════════════════════════════════
#  PAGES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
@app.route("/ui")
def index():
    return render_template("index.html", failures=chaos_config.get("failures", []))

# ══════════════════════════════════════════════════════════════════════════════
#  API  INSTANCES EC2
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/instances")
def api_instances():
    try:
        ec2  = _ec2()
        resp = ec2.describe_instances()
        instances = []
        for r in resp.get("Reservations", []):
            for i in r.get("Instances", []):
                tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
                instances.append({
                    "id":    i["InstanceId"],
                    "type":  i.get("InstanceType", "unknown"),
                    "state": i["State"]["Name"],
                    "chaos": tags.get("chaos", ""),
                    "tags":  tags,
                })
        return jsonify({"status": "success", "instances": instances})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/instance/<instance_id>/start", methods=["POST","GET"])
def api_start(instance_id):
    try:
        _ec2().start_instances(InstanceIds=[instance_id])
        log_event(f"▶️ Instance {instance_id} démarrée")
        return jsonify({"status": "success", "message": f"Instance {instance_id} démarrée"})
    except Exception as e:
        log_event(f"❌ start {instance_id} : {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/instance/<instance_id>/stop", methods=["POST","GET"])
def api_stop(instance_id):
    try:
        _ec2().stop_instances(InstanceIds=[instance_id])
        log_event(f"⏹️ Instance {instance_id} arrêtée")
        return jsonify({"status": "success", "message": f"Instance {instance_id} arrêtée"})
    except Exception as e:
        log_event(f"❌ stop {instance_id} : {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/instance/<instance_id>/terminate", methods=["POST","GET"])
def api_terminate(instance_id):
    try:
        _ec2().terminate_instances(InstanceIds=[instance_id])
        log_event(f"💥 Instance {instance_id} terminée")
        return jsonify({"status": "success", "message": f"Instance {instance_id} terminée"})
    except Exception as e:
        log_event(f"❌ terminate {instance_id} : {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════
#  API  S3
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/buckets")
def api_buckets():
    try:
        resp    = _s3().list_buckets()
        buckets = [b["Name"] for b in resp.get("Buckets", [])]
        return jsonify({"status": "success", "buckets": buckets})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/bucket/create/<name>", methods=["POST","GET"])
def api_create_bucket(name):
    try:
        _s3().create_bucket(Bucket=name)
        log_event(f"🔄 Bucket {name} créé")
        return jsonify({"status": "success", "message": f"Bucket '{name}' créé"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════
#  API  RÉSEAU
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/network")
def api_network():
    try:
        ec2     = _ec2()
        vpcs    = ec2.describe_vpcs().get("Vpcs", [])
        subnets = ec2.describe_subnets().get("Subnets", [])
        sgs     = ec2.describe_security_groups().get("SecurityGroups", [])
        return jsonify({
            "status":  "success",
            "vpcs":    [{"id": v["VpcId"], "cidr": v["CidrBlock"]} for v in vpcs],
            "subnets": [{"id": s["SubnetId"], "cidr": s["CidrBlock"], "vpc": s["VpcId"]} for s in subnets],
            "security_groups": [{"id": sg["GroupId"], "name": sg["GroupName"]} for sg in sgs],
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════
#  API  CHAOS
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/inject/<failure_id>", methods=["POST","GET"])
def api_inject(failure_id):
    failures = chaos_config.get("failures", [])
    meta     = next((f for f in failures if f["id"] == failure_id), None)
    if meta is None:
        return jsonify({"status": "error", "message": f"Panne inconnue : {failure_id}"}), 404
    log_event(f"🎯 [INJECT] Déclenchement : {meta['description']}")
    result = chaos_engine.execute_failure(failure_id)
    return jsonify(result)

# ══════════════════════════════════════════════════════════════════════════════
#  API  MONITORING & LOGS
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/monitor")
def api_monitor():
    try:
        monitoring.monitor_all()
        return jsonify({"status": "success", "message": "Monitoring effectué"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/api/logs")
def api_logs():
    if not os.path.exists(LOG_PATH):
        return jsonify({"status": "empty", "logs": []})
    with _log_lock:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    return jsonify({
        "status": "success",
        "logs": [l.strip() for l in lines[-100:] if l.strip()],
    })

# ══════════════════════════════════════════════════════════════════════════════
#  SSE  STREAMS
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/logs/stream")
def api_logs_stream():
    """SSE : nouvelles lignes de log en temps réel."""
    def generate():
        os.makedirs("logs", exist_ok=True)
        if not os.path.exists(LOG_PATH):
            open(LOG_PATH, "w").close()
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            f.seek(0, 2)          # se positionner à la fin
            while True:
                line = f.readline()
                if line and line.strip():
                    yield f"data: {json.dumps({'line': line.strip()})}\n\n"
                else:
                    time.sleep(0.4)
    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/instances/stream")
def api_instances_stream():
    """SSE : état des instances EC2 toutes les 3 s."""
    def generate():
        last = None
        while True:
            try:
                ec2  = _ec2()
                resp = ec2.describe_instances()
                instances = []
                for r in resp.get("Reservations", []):
                    for i in r.get("Instances", []):
                        tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
                        instances.append({
                            "id":    i["InstanceId"],
                            "type":  i.get("InstanceType", "unknown"),
                            "state": i["State"]["Name"],
                            "chaos": tags.get("chaos", ""),
                        })
                payload = json.dumps(instances)
                if payload != last:
                    last = payload
                    yield f"data: {payload}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            time.sleep(3)
    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/infra/stream")
def api_infra_stream():
    """SSE : état réseau + S3 toutes les 5 s."""
    def generate():
        last = None
        while True:
            try:
                ec2     = _ec2()
                s3c     = _s3()
                subnets = ec2.describe_subnets().get("Subnets", [])
                buckets = [b["Name"] for b in s3c.list_buckets().get("Buckets", [])]
                payload = json.dumps({
                    "subnets": [{"id": s["SubnetId"], "cidr": s["CidrBlock"]} for s in subnets],
                    "buckets": buckets,
                })
                if payload != last:
                    last = payload
                    yield f"data: {payload}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            time.sleep(5)
    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ══════════════════════════════════════════════════════════════════════════════
#  RÉTROCOMPATIBILITÉ
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/list-instances")
def list_instances():
    return api_instances()

@app.route("/list-buckets")
def list_buckets():
    return api_buckets()

@app.route("/inject/<failure_id>")
def inject_failure(failure_id):
    return api_inject(failure_id)

@app.route("/logs")
def logs():
    return api_logs()

@app.route("/monitor")
def monitor():
    return api_monitor()

@app.route("/create-bucket/<name>")
def create_bucket(name):
    return api_create_bucket(name)

@app.route("/start-instance/<instance_id>")
def start_instance(instance_id):
    return api_start(instance_id)

@app.route("/stop-instance/<instance_id>")
def stop_instance(instance_id):
    return api_stop(instance_id)

@app.route("/terminate-instance/<instance_id>")
def terminate_instance(instance_id):
    return api_terminate(instance_id)

# ══════════════════════════════════════════════════════════════════════════════
#  DÉMARRAGE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    if not os.path.exists(LOG_PATH):
        open(LOG_PATH, "w").close()

    subprocess.Popen(["python", "deploy.py"])
    subprocess.Popen(["python", "chaos_recovery.py"])
    subprocess.Popen(["python", "monitoring.py"])

    app.run(host="0.0.0.0", port=5005, threaded=True)
