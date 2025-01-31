import boto3
import argparse


def get_autoscaling_group(instance_id, autoscaling_client):
    """Retrieve the Auto Scaling Group name for a given instance."""
    asg_response = autoscaling_client.describe_auto_scaling_instances(InstanceIds=[instance_id])
    asg_instances = asg_response.get("AutoScalingInstances", [])
    if asg_instances:
        return asg_instances[0].get("AutoScalingGroupName", "N/A")
    return "N/A"


def get_instances_details(ec2_client, autoscaling_client, instance_ids=None, public_ips=None, vpc_ids=None):
    """Retrieve details of instances based on instance IDs, public IPs, or VPC IDs."""
    if vpc_ids:
        instance_ids = instance_ids or []
        for vpc_id in vpc_ids:
            response = ec2_client.describe_instances(
                Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
            )
            instance_ids.extend([instance["InstanceId"] for reservation in response["Reservations"] for instance in reservation["Instances"]])


    filters = []

    if instance_ids:
        filters.append({"Name": "instance-id", "Values": instance_ids})

    if public_ips:
        filters.append({"Name": "ip-address", "Values": public_ips})

    if not filters and not vpc_ids:
        raise ValueError("At least one of --public-ip, --instance-id, or --vpc-id must be provided.")

    response = ec2_client.describe_instances(Filters=filters)
    instances_data = []

    for reservation in response["Reservations"]:
        for instance in reservation["Instances"]:
            instance_id = instance["InstanceId"]
            state = instance["State"]["Name"]

            # Exclude instances that are stopped or terminated
            if state in ["stopped", "terminated"]:
                continue

            asg_name = get_autoscaling_group(instance_id, autoscaling_client)
            vpc_id = instance.get("VpcId")

            instance_data = {
                "InstanceId": instance_id,
                "PublicIpAddress": instance.get("PublicIpAddress", "N/A"),
                "State": instance["State"]["Name"],
                "LaunchTime": instance["LaunchTime"].strftime("%Y-%m-%d %H:%M:%S"),
                "VpcId": vpc_id,
                "InternetGatewayIds": [igw["InternetGatewayId"] for igw in ec2_client.describe_internet_gateways(
                    Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}])
                ["InternetGateways"]] if vpc_id else [],
                "SubnetIds": [subnet["SubnetId"] for subnet in
                              ec2_client.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
                              ["Subnets"]] if vpc_id else [],
                "NetworkInterfaceId": instance["NetworkInterfaces"][0]["NetworkInterfaceId"]
                if instance.get("NetworkInterfaces") else "N/A",
                "SecurityGroupIds": [
                    group["GroupId"] for group in instance.get("SecurityGroups", [])
                ],
                "IamInstanceProfile": instance.get("IamInstanceProfile", {}).get("Arn", "N/A"),
                "BlockDeviceMappings": [
                    {
                        "DeviceName": mapping["DeviceName"],
                        "VolumeId": mapping["Ebs"]["VolumeId"]
                    }
                    for mapping in instance.get("BlockDeviceMappings", [])
                ],
                "AutoScalingGroup": asg_name
            }

            instances_data.append(instance_data)

    return instances_data


def print_resource_info(instance_details):
    for instance in instance_details:
        print(f"Instance ID: {instance['InstanceId']}")
        print(f"Public IP: {instance['PublicIpAddress']}")
        print(f"State: {instance['State']}")
        print(f"Launch Time: {instance['LaunchTime']}")
        print(f"VPC ID: {instance['VpcId']}")
        print(f"IGW IDs: {instance['InternetGatewayIds']}")
        print(f"Subnet IDs: {instance['SubnetIds']}")
        print(f"Network Interface ID: {instance['NetworkInterfaceId']}")
        print(f"Security Group IDs: {instance['SecurityGroupIds']}")
        print(f"IAM Instance Profile: {instance['IamInstanceProfile']}")
        print(f"Block Device Mappings: {instance['BlockDeviceMappings']}")
        print(f"Auto Scaling Group: {instance['AutoScalingGroup']}")
        print()


def detach_iam_instance_profile(ec2_client, instance_profile_arn):
    if instance_profile_arn == "N/A":
        print("No IAM instance profile attached.")
        return  # Skip if no IAM profile

    instance_profile_name = instance_profile_arn.split('/')[-1]
    response = ec2_client.describe_iam_instance_profile_associations()
    print(f"Detaching IAM instance profile {instance_profile_name}...")
    for assoc in response["IamInstanceProfileAssociations"]:
        if assoc["IamInstanceProfile"]["Arn"] == instance_profile_arn:
            ec2_client.disassociate_iam_instance_profile(AssociationId=assoc["AssociationId"])



def terminate_instance(ec2_client, instance_id):
    if instance_id == "N/A":
        print("Invalid instance ID.")
        return  # Skip if instance ID is invalid

    ec2_client.terminate_instances(InstanceIds=[instance_id])
    print(f"Terminating instance {instance_id}...")
    waiter = ec2_client.get_waiter('instance_terminated')
    waiter.wait(InstanceIds=[instance_id])


# Function to detach and delete Auto Scaling Group (ASG) before terminating instances
def terminate_asg(autoscaling_client, asg_name):
    if asg_name == "N/A":
        print("No Auto Scaling Group attached.")
        return  # Skip if no ASG

    print(f"Terminating Auto Scaling Group {asg_name}...")
    autoscaling_client.delete_auto_scaling_group(AutoScalingGroupName=asg_name, ForceDelete=True)



# Function to delete subnets
def delete_subnet(ec2_client, subnet_id):
    if subnet_id == "N/A":
        print("Invalid subnet ID.")
        return  # Skip if subnet ID is invalid

    print(f"Deleting subnet {subnet_id}...")
    ec2_client.delete_subnet(SubnetId=subnet_id)


def delete_vpc(ec2_client, vpc_id):
    if vpc_id == "N/A":
        print("Invalid VPC ID.")
        return  # Skip if VPC ID is invalid

    delete_internet_gateways(ec2_client, vpc_id)
    delete_all_subnets(ec2_client, vpc_id)
    print(f"Deleting VPC {vpc_id}...")
    ec2_client.delete_vpc(VpcId=vpc_id)


# Function to delete internet gateways
def delete_internet_gateways(ec2_client, vpc_id):
    if vpc_id == "N/A":
        print("Invalid VPC ID.")
        return  # Skip if VPC ID is invalid

    response = ec2_client.describe_internet_gateways(Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}])
    for igw in response["InternetGateways"]:
        igw_id = igw["InternetGatewayId"]
        print(f"Detaching and deleting Internet Gateway {igw_id}...")
        ec2_client.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
        ec2_client.delete_internet_gateway(InternetGatewayId=igw_id)



# Function to delete all subnets in a VPC
def delete_all_subnets(ec2_client, vpc_id):
    if vpc_id == "N/A":
        print("Invalid VPC ID.")
        return  # Skip if VPC ID is invalid

    response = ec2_client.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    for subnet in response["Subnets"]:
        subnet_id = subnet["SubnetId"]
        delete_subnet(ec2_client, subnet_id)


# Iterate through the JSON data and delete resources in the correct order
def main():
    parser = argparse.ArgumentParser(description="Fetch AWS instance details based on Instance ID or Public IP.")

    parser.add_argument("--public-ips", nargs="+", help="Public IP(s) of the instances.")
    parser.add_argument("--instance-ids", nargs="+", help="Instance ID(s) of the instances.")
    parser.add_argument("--vpc-ids",   help="VPC IDs.")
    parser.add_argument("--region", help="AWS region.")
    parser.add_argument("--no-dry-run", action="store_true", help="Execute the deletion process.")

    args = parser.parse_args()

    ec2_client = boto3.client("ec2", region_name=args.region)  # Adjust the region as needed
    autoscaling_client = boto3.client("autoscaling", region_name=args.region)


    instances = get_instances_details(ec2_client, autoscaling_client, instance_ids=args.instance_ids,
                                      public_ips=args.public_ips, vpc_ids=args.vpc_ids)

    print("These resources will be deleted:")
    print_resource_info(instances)

    if not args.no_dry_run:
        print("Dry run completed. Use --no-dry-run to execute the deletion process.")
        return

    for instance_data in instances:
        if not instance_data:  # Skip empty entries
            continue

        instance_id = instance_data["InstanceId"]
        vpc_id = instance_data["VpcId"]
        iam_profile_arn = instance_data.get("IamInstanceProfile", "N/A")
        asg_name = instance_data["AutoScalingGroup"]

        terminate_asg(autoscaling_client=autoscaling_client, asg_name=asg_name)

        detach_iam_instance_profile(ec2_client=ec2_client, instance_profile_arn=iam_profile_arn)

        terminate_instance(ec2_client=ec2_client, instance_id=instance_id)

        delete_vpc(ec2_client=ec2_client, vpc_id=vpc_id)


if __name__ == "__main__":
    main()
