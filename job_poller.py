import time
import threading
import logging
import uuid
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from config import Config
from github_api_client import GitHubAPIClient
from aws_manager import AWSManager


class JobPoller:
    def __init__(self, config: Config):
        self.config = config
        self.github_api = GitHubAPIClient(config)
        self.aws = AWSManager(config)
        self.logger = logging.getLogger(__name__)
        
        # Track active runners and scale sets
        self.active_runners: Dict[str, Dict] = {}
        self.scale_sets: Dict[str, str] = {}  # name -> scale_set_id
        self.runners_lock = threading.Lock()
        
        # Polling control
        self.polling = False
        self.poll_thread = None

    def start_polling(self, org: str, poll_interval: int = 30):
        """Start polling for queued jobs"""
        if self.polling:
            self.logger.warning("Polling already started")
            return
        
        self.polling = True
        self.poll_thread = threading.Thread(
            target=self._poll_loop, 
            args=(org, poll_interval), 
            daemon=True
        )
        self.poll_thread.start()
        self.logger.info(f"Started polling for organization: {org}")

    def stop_polling(self):
        """Stop polling for jobs"""
        self.polling = False
        if self.poll_thread:
            self.poll_thread.join(timeout=5)
        self.logger.info("Stopped polling")

    def _poll_loop(self, org: str, poll_interval: int):
        """Main polling loop"""
        while self.polling:
            try:
                self._process_queued_jobs(org)
                self._cleanup_completed_runners()
                time.sleep(poll_interval)
            except Exception as e:
                self.logger.error(f"Error in polling loop: {e}")
                time.sleep(60)  # Wait longer on error

    def _process_queued_jobs(self, org: str):
        """Process queued jobs that need custom runners"""
        try:
            queued_jobs = self.github_api.get_queued_jobs(org)
            
            for job in queued_jobs:
                if self._should_create_runner_for_job(job):
                    self._create_runner_for_job(org, job)
                    
        except Exception as e:
            self.logger.error(f"Error processing queued jobs: {e}")

    def _should_create_runner_for_job(self, job: Dict) -> bool:
        """Check if we should create a runner for this job"""
        job_id = job["job_id"]
        labels = job.get("labels", [])
        
        # Check if job has our custom runs-on labels
        if not any("runs-on=" in str(label) for label in labels):
            return False
        
        # Check if we already have a runner for this job
        with self.runners_lock:
            for runner_info in self.active_runners.values():
                if runner_info.get("job_id") == job_id:
                    return False
        
        return True

    def _create_runner_for_job(self, org: str, job: Dict):
        """Create a runner for a specific job"""
        try:
            job_id = job["job_id"]
            labels = job.get("labels", [])
            repository = job["repository"]
            
            # Parse runner configuration from labels
            runner_config = self._parse_runner_config(labels)
            
            # Generate unique runner and scale set names
            runner_name = f"runner-{job_id}-{uuid.uuid4().hex[:8]}"
            scale_set_name = f"scaleset-{job_id}-{uuid.uuid4().hex[:8]}"
            
            # Create or get scale set
            scale_set_id = self._get_or_create_scale_set(org, scale_set_name)
            if not scale_set_id:
                self.logger.error(f"Failed to create scale set for job {job_id}")
                return
            
            # Get registration token for the organization
            registration_token = self.github_api.get_registration_token(org)
            
            if not registration_token:
                self.logger.error(f"Failed to get registration token for job {job_id}")
                return
            
            # Create AWS instance
            instance_config = {
                'instance_type': runner_config.get('instance_type', self.config.default_instance_type),
                'ami_id': runner_config.get('ami_id', self.config.default_ami_id),
                'max_price': runner_config.get('max_price', '0.10'),
                'registration_token': registration_token,
                'labels': runner_config.get('labels', self.config.runner_labels)
            }
            
            instance_id = self.aws.create_spot_instance(runner_name, instance_config)
            if not instance_id:
                self.logger.error(f"Failed to create instance for job {job_id}")
                # Clean up scale set if instance creation failed
                self.github_api.delete_scale_set(org, scale_set_id)
                return
            
            # Track the runner
            with self.runners_lock:
                self.active_runners[runner_name] = {
                    'job_id': job_id,
                    'instance_id': instance_id,
                    'scale_set_id': scale_set_id,
                    'scale_set_name': scale_set_name,
                    'repository': repository,
                    'created_at': datetime.now(),
                    'status': 'starting',
                    'config': runner_config
                }
            
            self.logger.info(f"Created runner {runner_name} for job {job_id}")
            
        except Exception as e:
            self.logger.error(f"Error creating runner for job {job_id}: {e}")

    def _get_or_create_scale_set(self, org: str, scale_set_name: str) -> Optional[str]:
        """Get existing or create new scale set"""
        try:
            # Check if we already have this scale set
            if scale_set_name in self.scale_sets:
                return self.scale_sets[scale_set_name]
            
            # For GitHub Enterprise, try to use existing scale sets first
            existing_scale_sets = self.github_api.list_scale_sets(org)
            if existing_scale_sets:
                # Use the first available scale set
                scale_set = existing_scale_sets[0]
                scale_set_id = str(scale_set.get("id"))
                self.scale_sets[scale_set_name] = scale_set_id
                self.logger.info(f"Using existing scale set {scale_set.get('name')} with ID {scale_set_id}")
                return scale_set_id
            
            # Try to create new scale set if none exist
            scale_set_id = self.github_api.create_scale_set(org, scale_set_name)
            if scale_set_id:
                self.scale_sets[scale_set_name] = scale_set_id
                self.logger.info(f"Created scale set {scale_set_name} with ID {scale_set_id}")
            else:
                self.logger.warning(f"Could not create scale set, this may affect runner registration")
            
            return scale_set_id
            
        except Exception as e:
            self.logger.error(f"Error getting/creating scale set {scale_set_name}: {e}")
            return None

    def _parse_runner_config(self, labels: List[str]) -> Dict:
        """Parse runner configuration from job labels"""
        config = {
            'instance_type': self.config.default_instance_type,
            'ami_id': self.config.default_ami_id,
            'labels': self.config.runner_labels.copy(),
            'max_price': '0.10',
            'work_folder': '/home/runner/_work'
        }
        
        for label in labels:
            if isinstance(label, str) and '=' in label:
                key, value = label.split('=', 1)
                
                if key == 'instanceType':
                    config['instance_type'] = value
                elif key == 'cpu':
                    config['instance_type'] = self._cpu_to_instance_type(int(value))
                elif key == 'memory' or key == 'ram':
                    # Could be used for instance selection
                    config['memory'] = value
                elif key == 'image':
                    config['ami_id'] = value
                elif key == 'maxPrice':
                    config['max_price'] = value
                elif key == 'workFolder':
                    config['work_folder'] = value
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
        
        for cpu, instance_type in sorted(cpu_map.items()):
            if cpu >= cpu_count:
                return instance_type
        
        return 'm5.16xlarge'

    def _cleanup_completed_runners(self):
        """Clean up runners that are no longer needed"""
        try:
            # Get current job statuses
            runners_to_cleanup = []
            
            with self.runners_lock:
                for runner_name, runner_info in self.active_runners.items():
                    # Check if runner is too old (timeout)
                    age = datetime.now() - runner_info['created_at']
                    if age > timedelta(seconds=self.config.runner_idle_timeout):
                        runners_to_cleanup.append(runner_name)
                        continue
                    
                    # Check if associated job is still queued/running
                    # This would require additional API calls to check job status
                    # For now, rely on timeout-based cleanup
            
            # Clean up identified runners
            for runner_name in runners_to_cleanup:
                self._cleanup_runner(runner_name)
                
        except Exception as e:
            self.logger.error(f"Error in cleanup: {e}")

    def _cleanup_runner(self, runner_name: str):
        """Clean up a specific runner"""
        try:
            with self.runners_lock:
                if runner_name not in self.active_runners:
                    return
                
                runner_info = self.active_runners[runner_name]
                instance_id = runner_info['instance_id']
                scale_set_id = runner_info['scale_set_id']
                scale_set_name = runner_info['scale_set_name']
            
            # Terminate EC2 instance
            self.aws.terminate_instance(instance_id)
            
            # Delete scale set (it's ephemeral per job)
            if scale_set_id:
                org = self.config.github_org
                if org:
                    self.github_api.delete_scale_set(org, scale_set_id)
                
                # Remove from cache
                if scale_set_name in self.scale_sets:
                    del self.scale_sets[scale_set_name]
            
            # Remove from tracking
            with self.runners_lock:
                if runner_name in self.active_runners:
                    del self.active_runners[runner_name]
            
            self.logger.info(f"Cleaned up runner {runner_name}")
            
        except Exception as e:
            self.logger.error(f"Error cleaning up runner {runner_name}: {e}")

    def get_status(self) -> Dict:
        """Get current status"""
        with self.runners_lock:
            return {
                'polling': self.polling,
                'active_runners': len(self.active_runners),
                'scale_sets': len(self.scale_sets),
                'runners': dict(self.active_runners)
            } 