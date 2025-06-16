import requests
import logging
import time
import json
from typing import Dict, Optional, List, Tuple
from config import Config


class GitHubAPIClient:
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.logger = logging.getLogger(__name__)
        
        # GitHub API endpoints
        self.gh_api_base = config.github_api_base
        self.github_base_url = config.github_base_url
        if config.github_org:
            self.runner_registration_url = f"{config.github_base_url}/{config.github_org}"
        else:
            self.runner_registration_url = None
        
        # Cache for tokens
        self._jwt_token = None
        self._pipeline_url = None
        self._jwt_expires = 0

    def get_registration_token(self, org: str) -> Optional[str]:
        """Get a registration token for the organization"""
        try:
            url = f"{self.gh_api_base}/orgs/{org}/actions/runners/registration-token"
            headers = {"Authorization": f"token {self.config.github_token}"}
            
            response = self.session.post(url, headers=headers)
            response.raise_for_status()
            
            return response.json().get("token")
        except Exception as e:
            self.logger.error(f"Failed to get registration token: {e}")
            return None

    def get_runner_admin_credentials(self, org: str) -> Tuple[Optional[str], Optional[str]]:
        """Get runner admin credentials and pipeline URL"""
        try:
            # Get registration token first
            registration_token = self.get_registration_token(org)
            if not registration_token:
                return None, None
            
            # Get runner admin credentials
            url = f"{self.gh_api_base}/actions/runner-registration"
            headers = {
                "Authorization": f"RemoteAuth {registration_token}",
                "Content-Type": "application/json"
            }
            
            data = {
                "url": f"{self.github_base_url}/{org}",
                "runnerEvent": "register"
            }
            
            response = self.session.post(url, headers=headers, json=data)
            response.raise_for_status()
            
            result = response.json()
            jwt_token = result.get("token")
            pipeline_url = result.get("url")
            
            # Cache the credentials with expiration
            self._jwt_token = jwt_token
            self._pipeline_url = pipeline_url
            self._jwt_expires = time.time() + 3600  # 1 hour
            
            return jwt_token, pipeline_url
            
        except Exception as e:
            self.logger.error(f"Failed to get runner admin credentials: {e}")
            return None, None

    def _ensure_valid_credentials(self, org: str) -> bool:
        """Ensure we have valid JWT credentials"""
        if (self._jwt_token is None or 
            self._pipeline_url is None or 
            time.time() >= self._jwt_expires):
            
            self.logger.info("Refreshing JWT credentials...")
            jwt_token, pipeline_url = self.get_runner_admin_credentials(org)
            return jwt_token is not None and pipeline_url is not None
        
        return True

    def list_scale_sets(self, org: str) -> List[Dict]:
        """List all runner scale sets"""
        try:
            if not self._ensure_valid_credentials(org):
                return []
            
            url = f"{self._pipeline_url}/_apis/runtime/runnerscalesets?api-version=6.0-preview"
            headers = {"Authorization": f"Bearer {self._jwt_token}"}
            
            response = self.session.get(url, headers=headers)
            response.raise_for_status()
            
            return response.json().get("value", [])
            
        except Exception as e:
            self.logger.error(f"Failed to list scale sets: {e}")
            return []

    def get_scale_set_usage(self, org: str, scale_set_id: str) -> Optional[Dict]:
        """Get usage statistics for a scale set"""
        try:
            if not self._ensure_valid_credentials(org):
                return None
            
            url = f"{self._pipeline_url}/_apis/runtime/runnerscalesets/{scale_set_id}/usage?api-version=6.0-preview"
            headers = {"Authorization": f"Bearer {self._jwt_token}"}
            
            response = self.session.get(url, headers=headers)
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            self.logger.error(f"Failed to get scale set usage: {e}")
            return None

    def create_scale_set(self, org: str, name: str, runner_group_id: int = 1) -> Optional[str]:
        """Create a new runner scale set"""
        try:
            if not self._ensure_valid_credentials(org):
                return None
            
            url = f"{self._pipeline_url}/_apis/runtime/runnerscalesets?api-version=6.0-preview"
            headers = {
                "Authorization": f"Bearer {self._jwt_token}",
                "Content-Type": "application/json"
            }
            
            data = {
                "name": name,
                "runnerGroupId": runner_group_id,
                "labels": self.config.runner_labels
            }
            
            response = self.session.post(url, headers=headers, json=data)
            response.raise_for_status()
            
            result = response.json()
            return str(result.get("id"))
            
        except Exception as e:
            self.logger.error(f"Failed to create scale set: {e}")
            return None

    def generate_jit_config(self, org: str, scale_set_id: str, runner_name: str, work_folder: str = "/home/runner/_work") -> Optional[str]:
        """Generate JIT (Just-In-Time) configuration for a runner"""
        try:
            if not self._ensure_valid_credentials(org):
                return None
            
            url = f"{self._pipeline_url}/_apis/runtime/runnerscalesets/{scale_set_id}/generatejitconfig?api-version=6.0-preview"
            headers = {
                "Authorization": f"Bearer {self._jwt_token}",
                "Content-Type": "application/json"
            }
            
            data = {
                "name": runner_name,
                "workFolder": work_folder
            }
            
            response = self.session.post(url, headers=headers, json=data)
            response.raise_for_status()
            
            result = response.json()
            return result.get("encodedJITConfig")
            
        except Exception as e:
            self.logger.error(f"Failed to generate JIT config: {e}")
            return None

    def delete_scale_set(self, org: str, scale_set_id: str) -> bool:
        """Delete a runner scale set"""
        try:
            if not self._ensure_valid_credentials(org):
                return False
            
            url = f"{self._pipeline_url}/_apis/runtime/runnerscalesets/{scale_set_id}?api-version=6.0-preview"
            headers = {"Authorization": f"Bearer {self._jwt_token}"}
            
            response = self.session.delete(url, headers=headers)
            response.raise_for_status()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to delete scale set: {e}")
            return False

    def get_queued_jobs(self, org: str, repo: str = None) -> List[Dict]:
        """Get queued workflow jobs for the organization or repository"""
        try:
            # For GitHub Enterprise, we need to check repositories individually
            # as the org-level endpoint might not be available
            queued_jobs = []
            
            if repo:
                repos_to_check = [f"{org}/{repo}"]
            else:
                # Get organization repositories
                repos_url = f"{self.gh_api_base}/orgs/{org}/repos"
                headers = {"Authorization": f"token {self.config.github_token}"}
                repos_response = self.session.get(repos_url, headers=headers)
                
                if repos_response.status_code != 200:
                    self.logger.warning(f"Could not list org repos, falling back to manual monitoring")
                    return []
                
                repos = repos_response.json()
                repos_to_check = [repo_data["full_name"] for repo_data in repos[:10]]  # Limit to first 10 repos
            
            headers = {"Authorization": f"token {self.config.github_token}"}
            
            for repo_full_name in repos_to_check:
                try:
                    # Get workflow runs for this repository
                    runs_url = f"{self.gh_api_base}/repos/{repo_full_name}/actions/runs"
                    params = {
                        "status": "queued",
                        "per_page": 50
                    }
                    
                    runs_response = self.session.get(runs_url, headers=headers, params=params)
                    if runs_response.status_code != 200:
                        continue
                    
                    runs = runs_response.json().get("workflow_runs", [])
                    
                    # Get jobs for each run
                    for run in runs:
                        jobs_url = f"{self.gh_api_base}/repos/{repo_full_name}/actions/runs/{run['id']}/jobs"
                        jobs_response = self.session.get(jobs_url, headers=headers)
                        if jobs_response.status_code == 200:
                            jobs = jobs_response.json().get("jobs", [])
                            for job in jobs:
                                if job.get("status") == "queued":
                                    # Check if this job requires our custom runners
                                    labels = job.get("labels", [])
                                    if any("runs-on=" in str(label) for label in labels):
                                        queued_jobs.append({
                                            "job_id": job["id"],
                                            "run_id": run["id"],
                                            "labels": labels,
                                            "repository": repo_full_name,
                                            "created_at": job["created_at"]
                                        })
                except Exception as repo_error:
                    self.logger.debug(f"Error checking repo {repo_full_name}: {repo_error}")
                    continue
            
            return queued_jobs
            
        except Exception as e:
            self.logger.error(f"Failed to get queued jobs: {e}")
            return [] 