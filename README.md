# AWS Resource Cleanup Script

## Overview
This script automates the process of retrieving AWS instance details and deleting associated resources such as EC2 instances, Auto Scaling Groups (ASG), Elastic Kubernetes Service (EKS) clusters, Virtual Private Clouds (VPCs), subnets, security groups, and IAM instance profiles. 

## Features
- Retrieve instance details using **Instance ID**, **Public IP**, or **VPC ID**.
- Identify associated **Auto Scaling Groups**, **Security Groups**, and **IAM Instance Profiles**.
- **Dry Run Mode** to preview deletions before executing them.
- **Automated Cleanup** of instances, subnets, internet gateways, and VPCs.
- **EKS Cluster Cleanup** before deleting the VPC.

## Requirements
### AWS Credentials
Set your AWS credentials as environment variables:
```sh
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
export AWS_SESSION_TOKEN=your-session-token # Optional, required for temporary credentials
```

### Python Dependencies
Ensure you have **Python 3** installed with the required libraries:
```sh
pip install boto3 argparse
```

## Usage
Run the script with different options to retrieve and clean up AWS resources.

### Retrieve Instance Details (Dry Run Mode)
```sh
python script.py --region us-east-1 --instance-id i-0123456789abcdef
```
```sh
python script.py --region us-east-1 --public-ip 34.123.45.67
```
```sh
python script.py --region us-east-1 --vpc-id vpc-12345678
```

### Execute Cleanup
To actually delete the resources, add the `--no-dry-run` flag:
```sh
python script.py --region us-east-1 --instance-id i-0123456789abcdef --no-dry-run
```
```sh
python script.py --region us-east-1 --vpc-id vpc-12345678 --no-dry-run
```

## Cleanup Process
The script performs the following steps in order:
1. **Retrieve AWS Resources**: Fetch instances, subnets, VPC, ASG, EKS clusters.
2. **Dry Run Confirmation**: Display resources that will be deleted.
3. **EKS Cluster Deletion** (if applicable).
4. **Auto Scaling Group Termination** (if applicable).
5. **Detach IAM Profiles** (if applicable).
6. **Terminate EC2 Instances**.
7. **Delete VPC Components**:
   - Subnets
   - Internet Gateways
   - VPC Endpoints
8. **Delete VPC**.

## Error Handling
- If no matching resources are found, the script prints a message and exits.
- If resources are partially deleted, it will attempt retries.
- If `--no-dry-run` is not specified, the script will only display resources without deleting them.

## Example Scenarios
### Deleting an Instance and Its Associated Resources
```sh
python script.py --region us-west-2 --instance-id i-0abcdef1234567890 --no-dry-run
```
### Deleting All Resources in a