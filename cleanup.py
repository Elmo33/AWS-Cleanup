import boto3
import argparse


def get_autoscaling_group(instance_id, autoscaling_client):
    """Retrieve the Auto Scaling Group name for a given instance."""
    asg_response = autoscaling_client.describe_auto_scaling_instances(InstanceIds=[instance_id])
    asg_instances = asg_response.get("AutoScalingInstances", [])
    if asg_instances:
        return asg_instances[0].get("AutoScalingGroupName", "N/A")
    return "N/A"


def get_instances_details(ec2_client, autoscaling_client, eks_client, instance_id=None, public_ip=None, vpc_id=None):
    """Retrieve details of instances based on instance IDs, public IPs, or VPC IDs."""
    resources = {
        "EKS Cluster": None,
        "VpcId": vpc_id,
        "Instances": []
    }

    if vpc_id:
        response = ec2_client.describe_instances(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )
        instance_ids = [
            instance["InstanceId"]
            for reservation in response["Reservations"]
            for instance in reservation["Instances"]
        ]

        if not instance_ids:
            print(f"No instances found for VPC {vpc_id}")

            # Fetch VPC details
            resources.update({
                "InternetGatewayIds": [
                    igw["InternetGatewayId"] for igw in ec2_client.describe_internet_gateways(
                        Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
                    )["InternetGateways"]
                ],
                "SubnetIds": [
                    subnet["SubnetId"] for subnet in ec2_client.describe_subnets(
                        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
                    )["Subnets"]
                ],
                "EKS Cluster": get_eks_cluster(eks_client, vpc_id),
            })
            return resources
    filters = []
    if instance_id:
        filters.append({"Name": "instance-id", "Values": instance_id})
    if public_ip:
        filters.append({"Name": "ip-address", "Values": public_ip})
    if not filters and not vpc_id:
        raise ValueError("At least one of --public-ip, --instance-id, or --vpc-id must be provided.")

    response = ec2_client.describe_instances(Filters=filters)

    for reservation in response["Reservations"]:
        for instance in reservation["Instances"]:
            instance_id = instance["InstanceId"]
            state = instance["State"]["Name"]

            if state in ["stopped", "terminated"]:
                continue

            asg_name = get_autoscaling_group(instance_id, autoscaling_client)
            vpc_id = instance.get("VpcId")

            instance_data = {
                "InstanceId": instance_id,
                "PublicIpAddress": instance.get("PublicIpAddress", "N/A"),
                "State": state,
                "LaunchTime": instance["LaunchTime"].strftime("%Y-%m-%d %H:%M:%S"),
                "VpcId": vpc_id,
                "InternetGatewayIds": [
                    igw["InternetGatewayId"] for igw in ec2_client.describe_internet_gateways(
                        Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
                    )["InternetGateways"]
                ] if vpc_id else [],
                "SubnetIds": [
                    subnet["SubnetId"] for subnet in ec2_client.describe_subnets(
                        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
                    )["Subnets"]
                ] if vpc_id else [],
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

            resources["Instances"].append(instance_data)

            # Ensure EKS Cluster is set if not already
            if not resources["EKS Cluster"]:
                resources["EKS Cluster"] = get_eks_cluster(eks_client, vpc_id)

    return resources


def print_resource_info(resources):
    print(f"EKS Cluster: {resources['EKS Cluster'] if resources['EKS Cluster'] else 'N/A'}")
    print(f"VPC ID: {resources['VpcId'] if resources['VpcId'] else 'N/A'}")

    if "InternetGatewayIds" in resources:
        print(
            f"Internet Gateway IDs: {', '.join(resources['InternetGatewayIds']) if resources['InternetGatewayIds'] else 'N/A'}")

    if "SubnetIds" in resources:
        print(f"Subnet IDs: {', '.join(resources['SubnetIds']) if resources['SubnetIds'] else 'N/A'}")

    print("\nInstances:")
    if not resources["Instances"]:
        print("No instances found.\n")
        return

    for instance in resources["Instances"]:
        for key, value in instance.items():
            print(f"{key}: {value if value else 'N/A'}")
        print()  # Blank line for separation


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

    delete_vpc_endpoints(ec2_client, vpc_id)
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

# Function to delete VPC endpoints
def delete_vpc_endpoints(ec2_client, vpc_id):
    if vpc_id == "N/A":
        print("Invalid VPC ID.")
        return  # Skip if VPC ID is invalid

    response = ec2_client.describe_vpc_endpoints(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    for endpoint in response["VpcEndpoints"]:
        endpoint_id = endpoint["VpcEndpointId"]
        print(f"Deleting VPC Endpoint {endpoint_id}...")
        ec2_client.delete_vpc_endpoints(VpcEndpointIds=[endpoint_id])


def get_eks_cluster(eks_client, vpc_id):
    """Retrieve the EKS cluster associated with the given VPC."""
    response = eks_client.list_clusters()
    for cluster_name in response.get("clusters", []):
        cluster_desc = eks_client.describe_cluster(name=cluster_name)
        if cluster_desc["cluster"]["resourcesVpcConfig"]["vpcId"] == vpc_id:
            return cluster_name
    return None


def delete_eks_cluster(eks_client, eks_cluster_name):
    """Delete all node groups before deleting the EKS cluster."""
    if eks_cluster_name:
        print(f"Deleting node groups for EKS cluster {eks_cluster_name}...")
        node_groups = eks_client.list_nodegroups(clusterName=eks_cluster_name)["nodegroups"]
        for node_group in node_groups:
            eks_client.delete_nodegroup(clusterName=eks_cluster_name, nodegroupName=node_group)
            print(f"Deleted node group {node_group}")

        waiter = eks_client.get_waiter('nodegroup_deleted')
        for node_group in node_groups:
            waiter.wait(clusterName=eks_cluster_name, nodegroupName=node_group)

        print(f"Deleting EKS cluster {eks_cluster_name}...")
        eks_client.delete_cluster(name=eks_cluster_name)


# Iterate through the JSON data and delete resources in the correct order
def main():
    parser = argparse.ArgumentParser(description="Fetch AWS instance details based on Instance ID or Public IP.")

    parser.add_argument("--public-ip", nargs="+", help="Public IP(s) of the instances.")
    parser.add_argument("--instance-id", nargs="+", help="Instance ID(s) of the instances.")
    parser.add_argument("--vpc-id", help="VPC IDs.")
    parser.add_argument("--region", help="AWS region.")
    parser.add_argument("--no-dry-run", action="store_true", help="Execute the deletion process.")

    args = parser.parse_args()

    ec2_client = boto3.client("ec2", region_name=args.region)  # Adjust the region as needed
    autoscaling_client = boto3.client("autoscaling", region_name=args.region)
    eks_client = boto3.client("eks", region_name=args.region)

    vpc_id = args.vpc_id
    print("retrieving details...")
    resources = get_instances_details(
        ec2_client=ec2_client,
        autoscaling_client=autoscaling_client,
        eks_client=eks_client,
        instance_id=args.instance_id,
        public_ip=args.public_ip, vpc_id=args.vpc_id
    )

    print("These resources will be deleted:")
    print_resource_info(resources)

    if not args.no_dry_run:
        print("Dry run completed. Use --no-dry-run to execute the deletion process.")
        return

    if resources["EKS Cluster"]:
        delete_eks_cluster(eks_client=eks_client, eks_cluster_name=resources["EKS Cluster"])

        print("Updating the resources after removing the EKS cluster")
        resources = get_instances_details(
            ec2_client=ec2_client,
            autoscaling_client=autoscaling_client,
            eks_client=eks_client,
            instance_id=args.instance_id,
            public_ip=args.public_ip, vpc_id=args.vpc_id
        )
        print("Resources left for deletion:")
        print_resource_info(resources)

    for instance_data in resources["Instances"]:
        if not instance_data:  # Skip empty entries
            continue

        instance_id = instance_data.get("InstanceId", "N/A")
        vpc_id = instance_data.get("VpcId", "N/A") if vpc_id is None else vpc_id
        iam_profile_arn = instance_data.get("IamInstanceProfile", "N/A")
        asg_name = instance_data.get("AutoScalingGroup", "N/A")

        terminate_asg(autoscaling_client=autoscaling_client, asg_name=asg_name)

        detach_iam_instance_profile(ec2_client=ec2_client, instance_profile_arn=iam_profile_arn)

        terminate_instance(ec2_client=ec2_client, instance_id=instance_id)

    if vpc_id:
        delete_vpc(ec2_client=ec2_client, vpc_id=vpc_id)


if __name__ == "__main__":
    main()
