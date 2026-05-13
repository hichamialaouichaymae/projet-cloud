import os
import json
import time
import boto3
from botocore.exceptions import ClientError

STATE_FILE = os.getenv("STATE_FILE", "state.json")

BUCKET_NAME = os.getenv("BUCKET_NAME", "demo-bucket-chaos-v2")
AMI_ID = os.getenv("AMI_ID", "ami-test")  # valeur par défaut pour LocalStack
INSTANCE_TYPE = os.getenv("INSTANCE_TYPE", "t3.micro")


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def deploy_s3(s3):
    try:
        s3.create_bucket(Bucket=BUCKET_NAME)
        print(f"[deploy] Bucket {BUCKET_NAME} créé")
    except ClientError as e:
        if "BucketAlreadyOwnedByYou" not in str(e) and "BucketAlreadyExists" not in str(e):
            raise
        print(f"[deploy] Bucket {BUCKET_NAME} déjà existant")
    return {"bucket": BUCKET_NAME}


def deploy_network(ec2):
    vpc_id = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnet_id = ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.1.0/24")["Subnet"]["SubnetId"]

    sg_id = ec2.create_security_group(
        GroupName="chaos-sg",
        Description="Security group for chaos v2 demo",
        VpcId=vpc_id,
    )["GroupId"]

    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
            {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
        ],
    )

    print(f"[deploy] VPC {vpc_id}, Subnet {subnet_id}, SG {sg_id} créés")
    return {"vpc_id": vpc_id, "subnet_id": subnet_id, "sg_id": sg_id}


def deploy_instance(ec2, subnet_id: str, sg_id: str):
    resp = ec2.run_instances(
        ImageId=AMI_ID,
        InstanceType=INSTANCE_TYPE,
        MinCount=1,
        MaxCount=1,
        NetworkInterfaces=[{"DeviceIndex": 0, "SubnetId": subnet_id, "Groups": [sg_id], "AssociatePublicIpAddress": True}],
        TagSpecifications=[{"ResourceType": "instance", "Tags": [{"Key": "chaos", "Value": "true"}]}],
    )
    instance_id = resp["Instances"][0]["InstanceId"]

    for _ in range(20):
        r = ec2.describe_instances(InstanceIds=[instance_id])
        state = r["Reservations"][0]["Instances"][0]["State"]["Name"]
        if state == "running":
            break
        time.sleep(2)

    print(f"[deploy] Instance {instance_id} lancée et en état 'running'")
    return {"instance_id": instance_id}


def main():
    s3 = boto3.client("s3", endpoint_url=AWS_ENDPOINT, region_name=AWS_REGION,
                      aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)

    ec2 = boto3.client("ec2", endpoint_url=AWS_ENDPOINT, region_name=AWS_REGION,
                       aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)
    # Créer un VPC
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    print("VPC créé :", vpc_id)
    #Créer un sous-réseau
    subnet = ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.1.0/24")
    subnet_id = subnet["Subnet"]["SubnetId"]
    print("Subnet créé :", subnet_id)



    state = {"endpoint": AWS_ENDPOINT, "region": AWS_REGION}

    print("[deploy] S3...")
    state.update(deploy_s3(s3))

    print("[deploy] Network...")
    state.update(deploy_network(ec2))

    print("[deploy] Instance...")
    state.update(deploy_instance(ec2, state["subnet_id"], state["sg_id"]))

    save_state(state)
    print("[deploy] Done. State saved to", STATE_FILE)
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()