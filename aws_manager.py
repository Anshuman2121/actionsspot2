import boto3
import time
import logging
import base64
from typing import Dict, Optional, List
from botocore.exceptions import ClientError
from config import Config


class AWSManager:
    def __init__(self, config: Config):
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # Initialize EC2 client
        session = boto3.Session(
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
            region_name=config.aws_region
        )
        self.ec2 = session.client('ec2')
        self.ec2_resource = session.resource('ec2')

    def create_spot_instance(self, runner_name: str, instance_config: Dict) -> Optional[str]:
        """Create a spot instance for GitHub Actions runner"""
        try:
            # Extract runner token and labels from config
            runner_token = instance_config.get('registration_token', '')
            labels = instance_config.get('labels', self.config.runner_labels)
            runner_labels_str = ','.join(labels) if isinstance(labels, list) else str(labels)
            
            user_data = self._generate_user_data(runner_name, runner_token, runner_labels_str)
            
            # Spot instance request specification
            launch_spec = {
                'ImageId': instance_config.get('ami_id', self.config.default_ami_id),
                'InstanceType': instance_config.get('instance_type', self.config.default_instance_type),
                'UserData': base64.b64encode(user_data.encode()).decode(),
                'SecurityGroupIds': self.config.security_group_ids
            }
            
            # Add subnet if specified
            if self.config.subnet_id:
                launch_spec['SubnetId'] = self.config.subnet_id
            
            # Add key pair if specified
            if self.config.key_pair_name:
                launch_spec['KeyName'] = self.config.key_pair_name
            
            # Request spot instance
            response = self.ec2.request_spot_instances(
                SpotPrice=instance_config.get('max_price', '0.10'),
                InstanceCount=1,
                Type='one-time',
                LaunchSpecification=launch_spec
            )
            
            spot_request_id = response['SpotInstanceRequests'][0]['SpotInstanceRequestId']
            self.logger.info(f"Spot instance request created: {spot_request_id}")
            
            # Wait for spot request to be fulfilled
            instance_id = self._wait_for_spot_fulfillment(spot_request_id)
            if instance_id:
                # Tag the instance after creation
                self._tag_instance(instance_id, runner_name)
                self.logger.info(f"Spot instance created and tagged: {instance_id}")
                return instance_id
            else:
                self.logger.error("Spot instance request was not fulfilled")
                return None
                
        except ClientError as e:
            self.logger.error(f"Failed to create spot instance: {e}")
            return None

    def _wait_for_spot_fulfillment(self, spot_request_id: str, timeout: int = 300) -> Optional[str]:
        """Wait for spot request to be fulfilled and return instance ID"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                response = self.ec2.describe_spot_instance_requests(
                    SpotInstanceRequestIds=[spot_request_id]
                )
                
                request = response['SpotInstanceRequests'][0]
                state = request['State']
                
                if state == 'active' and 'InstanceId' in request:
                    return request['InstanceId']
                elif state in ['cancelled', 'failed']:
                    self.logger.error(f"Spot request {spot_request_id} failed with state: {state}")
                    return None
                
                time.sleep(10)
                
            except ClientError as e:
                self.logger.error(f"Error checking spot request status: {e}")
                return None
        
        self.logger.error(f"Timeout waiting for spot request {spot_request_id}")
        return None

    def _generate_user_data(self, runner_name: str, runner_token: str, runner_labels_str: str) -> str:
        """Generate user data script for runner instance"""
        return f"""#!/bin/bash
cd /actions-runner
# Set up the runner
RUNNER_ALLOW_RUNASROOT=1 ./config.sh --url {self.config.github_base_url}/{self.config.github_org} --token {runner_token} --name {runner_name} --labels {runner_labels_str} --ephemeral --runnergroup SpotInstances --work _work --replace
# Install and start the runner service
./svc.sh install
./svc.sh start
"""

    def _tag_instance(self, instance_id: str, runner_name: str):
        """Tag an EC2 instance with metadata"""
        try:
            tags = [
                {'Key': 'Name', 'Value': f'github-runner-{runner_name}'},
                {'Key': 'Type', 'Value': 'github-actions-runner'},
                {'Key': 'RunnerName', 'Value': runner_name},
                {'Key': 'ManagedBy', 'Value': 'github-runner-manager'}
            ]
            
            self.ec2.create_tags(
                Resources=[instance_id],
                Tags=tags
            )
            self.logger.info(f"Tagged instance {instance_id} with runner metadata")
            
        except ClientError as e:
            self.logger.warning(f"Failed to tag instance {instance_id}: {e}")
            # Don't fail the whole process if tagging fails

    def terminate_instance(self, instance_id: str) -> bool:
        """Terminate an EC2 instance"""
        try:
            self.ec2.terminate_instances(InstanceIds=[instance_id])
            self.logger.info(f"Instance {instance_id} terminated")
            return True
        except ClientError as e:
            self.logger.error(f"Failed to terminate instance {instance_id}: {e}")
            return False

    def get_instance_status(self, instance_id: str) -> Optional[str]:
        """Get the status of an EC2 instance"""
        try:
            response = self.ec2.describe_instances(InstanceIds=[instance_id])
            instances = response['Reservations'][0]['Instances']
            if instances:
                return instances[0]['State']['Name']
        except ClientError as e:
            self.logger.error(f"Failed to get instance status for {instance_id}: {e}")
        return None

    def list_runner_instances(self) -> List[Dict]:
        """List all instances tagged as GitHub Actions runners"""
        try:
            response = self.ec2.describe_instances(
                Filters=[
                    {'Name': 'tag:Type', 'Values': ['github-actions-runner']},
                    {'Name': 'instance-state-name', 'Values': ['running', 'pending']}
                ]
            )
            
            instances = []
            for reservation in response['Reservations']:
                for instance in reservation['Instances']:
                    runner_name = None
                    for tag in instance.get('Tags', []):
                        if tag['Key'] == 'RunnerName':
                            runner_name = tag['Value']
                            break
                    
                    instances.append({
                        'instance_id': instance['InstanceId'],
                        'runner_name': runner_name,
                        'state': instance['State']['Name'],
                        'launch_time': instance['LaunchTime'],
                        'instance_type': instance['InstanceType']
                    })
            
            return instances
        except ClientError as e:
            self.logger.error(f"Failed to list runner instances: {e}")
            return []

    def cleanup_old_instances(self, max_age_hours: int = 2) -> int:
        """Clean up instances older than specified hours"""
        instances = self.list_runner_instances()
        cleaned_count = 0
        
        current_time = time.time()
        
        for instance in instances:
            launch_time = instance['launch_time'].timestamp()
            age_hours = (current_time - launch_time) / 3600
            
            if age_hours > max_age_hours:
                self.logger.info(f"Cleaning up old instance {instance['instance_id']} (age: {age_hours:.1f}h)")
                if self.terminate_instance(instance['instance_id']):
                    cleaned_count += 1
        
        return cleaned_count 