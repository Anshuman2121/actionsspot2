import requests
import logging
from typing import Dict, Optional, Tuple
from config import Config


class GitHubClient:
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {config.github_token}",
            "Accept": "application/vnd.github.v3+json"
        })
        self.logger = logging.getLogger(__name__)

    def get_runner_registration_token(self, owner: str, repo: str) -> Optional[str]:
        """Get a registration token for a self-hosted runner"""
        try:
            url = f"https://api.github.com/repos/{owner}/{repo}/actions/runners/registration-token"
            response = self.session.post(url)
            response.raise_for_status()
            return response.json().get("token")
        except Exception as e:
            self.logger.error(f"Failed to get runner registration token: {e}")
            return None

    def get_runner_remove_token(self, owner: str, repo: str) -> Optional[str]:
        """Get a remove token for a self-hosted runner"""
        try:
            url = f"https://api.github.com/repos/{owner}/{repo}/actions/runners/remove-token"
            response = self.session.post(url)
            response.raise_for_status()
            return response.json().get("token")
        except Exception as e:
            self.logger.error(f"Failed to get runner remove token: {e}")
            return None

    def list_runners(self, owner: str, repo: str) -> list:
        """List all self-hosted runners for a repository"""
        try:
            url = f"https://api.github.com/repos/{owner}/{repo}/actions/runners"
            response = self.session.get(url)
            response.raise_for_status()
            return response.json().get("runners", [])
        except Exception as e:
            self.logger.error(f"Failed to list runners: {e}")
            return []

    def remove_runner(self, owner: str, repo: str, runner_id: int) -> bool:
        """Remove a self-hosted runner"""
        try:
            url = f"https://api.github.com/repos/{owner}/{repo}/actions/runners/{runner_id}"
            response = self.session.delete(url)
            response.raise_for_status()
            return True
        except Exception as e:
            self.logger.error(f"Failed to remove runner {runner_id}: {e}")
            return False

    def get_workflow_job(self, owner: str, repo: str, job_id: int) -> Optional[Dict]:
        """Get workflow job details"""
        try:
            url = f"https://api.github.com/repos/{owner}/{repo}/actions/jobs/{job_id}"
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Failed to get workflow job {job_id}: {e}")
            return None

    def parse_repository_from_url(self, repo_url: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse owner and repo from GitHub repository URL"""
        try:
            # Handle different URL formats
            if repo_url.startswith("https://github.com/"):
                parts = repo_url.replace("https://github.com/", "").split("/")
                if len(parts) >= 2:
                    return parts[0], parts[1]
            elif "/" in repo_url:
                parts = repo_url.split("/")
                if len(parts) >= 2:
                    return parts[0], parts[1]
        except Exception as e:
            self.logger.error(f"Failed to parse repository URL {repo_url}: {e}")
        
        return None, None 