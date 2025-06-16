import re
import time
import uuid
import logging
import threading
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
from config import Config
from github_client import GitHubClient
from aws_manager import AWSManager


class RunnerManager:
    def __init__(self, config: Config):
        self.config = config
        self.github = GitHubClient(config)
        self.aws = AWSManager(config)
        self.logger = logging.getLogger(__name__)
        
        # Track active runners
        self.active_runners: Dict[str, Dict] = {}
        self.runners_lock = threading.Lock()
        
        # Start cleanup thread
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self.cleanup_thread.start()

    def parse_runs_on_config(self, runs_on: list) -> Dict:
        """Parse runs-on configuration from workflow job"""
        config = {
            'instance_type': self.config.default_instance_type,
            'ami_id': self.config.default_ami_id,
            'labels': self.config.runner_labels.copy(),
            'max_price': '0.10'
        }
        
        for item in runs_on:
            if isinstance(item, str):
                # Handle runs-on=<run_id> format
                if item.startswith('runs-on='):
                    # Extract run_id for uniqueness
                    run_id = item.split('=')[1]
                    config['run_id'] = run_id
                
                # Handle instanceType=<type> format
                elif item.startswith('instanceType='):
                    instance_type = item.split('=')[1]
                    config['instance_type'] = instance_type
                
                # Handle other configurations
                elif '=' in item:
                    key, value = item.split('=', 1)
                    if key == 'family':
                        config['instance_type'] = value
                    elif key == 'cpu':
                        # Map CPU count to instance type
                        config['instance_type'] = self._cpu_to_instance_type(int(value))
                    elif key == 'memory' or key == 'ram':
                        # Could be used to select appropriate instance type
                        config['memory'] = value
                    elif key == 'image':
                        config['ami_id'] = value
                    elif key == 'maxPrice':
                        config['max_price'] = value
                    elif key == 'labels':
                        config['labels'] = value.split(',')
        
        return config

    def _cpu_to_instance_type(self, cpu_count: int) -> str:
        """Map CPU count to appropriate instance type"""
        cpu_map = {
            1: 't3.micro',
            2: 't3.medium',
            4: 't3.xlarge',
            8: 't3.2xlarge',
            16: 'm5.4xlarge',
            32: 'm5.8xlarge',
            64: 'm5.16xlarge'
        }
        
        # Find the closest match
        for cpu, instance_type in sorted(cpu_map.items()):
            if cpu >= cpu_count:
                return instance_type
        
        return 'm5.16xlarge'  # Default to largest if not found

    def handle_workflow_job_queued(self, payload: Dict) -> bool:
        """Handle workflow_job queued event"""
        try:
            job = payload.get('workflow_job', {})
            job_id = job.get('id')
            runs_on = job.get('labels', [])
            repo_url = payload.get('repository', {}).get('html_url', '')
            
            # Check if this is our self-hosted runner request
            if not any('runs-on=' in str(label) for label in runs_on):
                self.logger.debug(f"Job {job_id} is not for our runners")
                return False
            
            # Parse repository info
            owner, repo = self.github.parse_repository_from_url(repo_url)
            if not owner or not repo:
                self.logger.error(f"Could not parse repository from {repo_url}")
                return False
            
            # Parse runner configuration
            runner_config = self.parse_runs_on_config(runs_on)
            
            # Generate unique runner name
            runner_name = f"runner-{job_id}-{uuid.uuid4().hex[:8]}"
            
            # Create runner
            success = self.create_runner(runner_name, owner, repo, runner_config)
            
            if success:
                self.logger.info(f"Successfully created runner {runner_name} for job {job_id}")
                return True
            else:
                self.logger.error(f"Failed to create runner for job {job_id}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error handling workflow job queued: {e}")
            return False

    def create_runner(self, runner_name: str, owner: str, repo: str, runner_config: Dict) -> bool:
        """Create a new GitHub Actions runner"""
        try:
            # Get registration token
            reg_token = self.github.get_runner_registration_token(owner, repo)
            if not reg_token:
                self.logger.error("Failed to get registration token")
                return False
            
            # Prepare instance configuration
            instance_config = {
                'instance_type': runner_config.get('instance_type', self.config.default_instance_type),
                'ami_id': runner_config.get('ami_id', self.config.default_ami_id),
                'max_price': runner_config.get('max_price', '0.10'),
                'owner': owner,
                'repo': repo,
                'registration_token': reg_token,
                'labels': runner_config.get('labels', self.config.runner_labels),
                'github_token': self.config.github_token
            }
            
            # Create spot instance
            instance_id = self.aws.create_spot_instance(runner_name, instance_config)
            if not instance_id:
                self.logger.error("Failed to create spot instance")
                return False
            
            # Track the runner
            with self.runners_lock:
                self.active_runners[runner_name] = {
                    'instance_id': instance_id,
                    'owner': owner,
                    'repo': repo,
                    'created_at': datetime.now(),
                    'status': 'starting',
                    'config': runner_config
                }
            
            self.logger.info(f"Runner {runner_name} created with instance {instance_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error creating runner {runner_name}: {e}")
            return False

    def handle_workflow_job_completed(self, payload: Dict) -> bool:
        """Handle workflow_job completed/cancelled event"""
        try:
            job = payload.get('workflow_job', {})
            job_id = job.get('id')
            runs_on = job.get('labels', [])
            
            # Find runner for this job
            runner_name = None
            with self.runners_lock:
                for name, info in self.active_runners.items():
                    if f"runner-{job_id}" in name:
                        runner_name = name
                        break
            
            if runner_name:
                self.cleanup_runner(runner_name)
                self.logger.info(f"Cleaned up runner {runner_name} for completed job {job_id}")
                return True
            else:
                self.logger.debug(f"No runner found for job {job_id}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error handling workflow job completion: {e}")
            return False

    def cleanup_runner(self, runner_name: str) -> bool:
        """Clean up a specific runner"""
        try:
            with self.runners_lock:
                if runner_name not in self.active_runners:
                    return False
                
                runner_info = self.active_runners[runner_name]
                instance_id = runner_info['instance_id']
                owner = runner_info['owner']
                repo = runner_info['repo']
            
            # Remove from GitHub (get remove token and remove runner)
            remove_token = self.github.get_runner_remove_token(owner, repo)
            if remove_token:
                # Find runner ID from GitHub API
                runners = self.github.list_runners(owner, repo)
                for runner in runners:
                    if runner['name'] == runner_name:
                        self.github.remove_runner(owner, repo, runner['id'])
                        break
            
            # Terminate EC2 instance
            self.aws.terminate_instance(instance_id)
            
            # Remove from tracking
            with self.runners_lock:
                if runner_name in self.active_runners:
                    del self.active_runners[runner_name]
            
            self.logger.info(f"Successfully cleaned up runner {runner_name}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error cleaning up runner {runner_name}: {e}")
            return False

    def _cleanup_loop(self):
        """Background thread to clean up old runners"""
        while True:
            try:
                self._cleanup_old_runners()
                self._cleanup_orphaned_instances()
                time.sleep(300)  # Run every 5 minutes
            except Exception as e:
                self.logger.error(f"Error in cleanup loop: {e}")
                time.sleep(60)

    def _cleanup_old_runners(self):
        """Clean up runners older than the idle timeout"""
        cutoff_time = datetime.now() - timedelta(seconds=self.config.runner_idle_timeout)
        
        runners_to_cleanup = []
        with self.runners_lock:
            for name, info in self.active_runners.items():
                if info['created_at'] < cutoff_time:
                    runners_to_cleanup.append(name)
        
        for runner_name in runners_to_cleanup:
            self.logger.info(f"Cleaning up old runner {runner_name}")
            self.cleanup_runner(runner_name)

    def _cleanup_orphaned_instances(self):
        """Clean up EC2 instances that are no longer tracked"""
        try:
            # Get all runner instances from AWS
            aws_instances = self.aws.list_runner_instances()
            
            # Get tracked runner instance IDs
            tracked_instances = set()
            with self.runners_lock:
                for info in self.active_runners.values():
                    tracked_instances.add(info['instance_id'])
            
            # Clean up untracked instances older than 1 hour
            cutoff_time = datetime.now() - timedelta(hours=1)
            
            for instance in aws_instances:
                if (instance['instance_id'] not in tracked_instances and 
                    instance['launch_time'] < cutoff_time):
                    self.logger.info(f"Cleaning up orphaned instance {instance['instance_id']}")
                    self.aws.terminate_instance(instance['instance_id'])
                    
        except Exception as e:
            self.logger.error(f"Error cleaning up orphaned instances: {e}")

    def get_status(self) -> Dict:
        """Get current status of all runners"""
        with self.runners_lock:
            return {
                'active_runners': len(self.active_runners),
                'runners': dict(self.active_runners)
            } 