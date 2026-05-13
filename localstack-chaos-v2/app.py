
import os
import subprocess
from flask import Flask, jsonify, render_template
import boto3
from prometheus_flask_exporter import PrometheusMetrics
from utils.helpers import load_json, log_event
import monitoring

# Initialisation Flask
app = Flask(__name__)

# Prometheus
metrics = PrometheusMetrics(app)
metrics.info("flask_app_info", "Application Flask avec LocalStack", version="2.0.0")

# Config AWS (LocalStack)
AWS_ENDPOINT = os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566")
AWS_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID", "test")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "test")

# Clients AWS simulés
s3_client = boto3.client(
    "s3",
    endpoint_url=AWS_ENDPOINT,
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY
)

client_ec2 = boto3.client(
    "ec2",
    endpoint_url=AWS_ENDPOINT,
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY
)

# Charger les pannes depuis chaos_config.json
chaos_config = load_json("config/chaos_config.json", default={"failures": []})

# --- Lancer les autres modules en parallèle ---
subprocess.Popen(["python", "deploy.py"])
subprocess.Popen(["python", "chaos_recovery.py"])
subprocess.Popen(["python", "monitoring.py"])

# --- Routes Flask ---
@app.route("/")
def index():
    return render_template("index.html", failures=chaos_config.get("failures", []))

# Buckets
@app.route("/create-bucket/<name>")
def create_bucket(name: str):
    try:
        s3_client.create_bucket(Bucket=name)
        log_event(f"🔄 Bucket {name} créé")
        return jsonify({"message": f"✅ Bucket '{name}' créé"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/list-buckets")
def list_buckets():
    try:
        response = s3_client.list_buckets()
        buckets = [bucket["Name"] for bucket in response.get("Buckets", [])]
        return jsonify({"buckets": buckets})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Instances EC2
@app.route("/list-instances")
def list_instances():
    try:
        resp = client_ec2.describe_instances()
        vms = [{"id": i["InstanceId"], "state": i["State"]["Name"]}
               for r in resp["Reservations"] for i in r["Instances"]]
        return jsonify({"instances": vms})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/start-instance/<instance_id>")
def start_instance(instance_id):
    client_ec2.start_instances(InstanceIds=[instance_id])
    log_event(f"▶️ Instance {instance_id} démarrée")
    return jsonify({"message": f"Instance {instance_id} démarrée"})

@app.route("/stop-instance/<instance_id>")
def stop_instance(instance_id):
    client_ec2.stop_instances(InstanceIds=[instance_id])
    log_event(f"⏹️ Instance {instance_id} arrêtée")
    return jsonify({"message": f"Instance {instance_id} arrêtée"})

@app.route("/terminate-instance/<instance_id>")
def terminate_instance(instance_id):
    client_ec2.terminate_instances(InstanceIds=[instance_id])
    log_event(f"💥 Instance {instance_id} terminée")
    return jsonify({"message": f"Instance {instance_id} terminée"})

# Injection de pannes
@app.route("/inject/<failure_id>")
def inject_failure(failure_id):
    failures = chaos_config.get("failures", [])
    failure = next((f for f in failures if f["id"] == failure_id), None)
    if not failure:
        return jsonify({"status": "error", "message": f"Panne {failure_id} inconnue"}), 404
    log_event(f"💥 Panne injectée : {failure['description']}")
    return jsonify({"status": "success", "message": f"Panne {failure['description']} déclenchée"})

# Monitoring
@app.route("/monitor")
def monitor():
    try:
        monitoring.monitor_all()
        return jsonify({"status": "success", "message": "Monitoring effectué, voir logs"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# Logs
@app.route("/logs")
def logs():
    log_path = "logs/events.log"
    if not os.path.exists(log_path):
        return jsonify({"status": "empty", "logs": []})
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return jsonify({"status": "success", "logs": [line.strip() for line in lines[-50:]]})

# Interface Web
@app.route("/ui")
def ui():
    return render_template("index.html", failures=chaos_config.get("failures", []))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5005)
