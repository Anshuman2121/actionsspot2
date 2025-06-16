import os
from typing import Optional
from pydantic import BaseModel


class Config(BaseModel):
    # GitHub Configuration
    github_token: str
    github_org: str
    github_repo: Optional[str] = None
    github_api_base: str = "https://api.github.com"
    github_base_url: str = "https://github.com"
    
    # AWS Configuration
    aws_region: str = "us-east-1"
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    
    # EC2 Configuration
    default_instance_type: str = "t3.medium"
    default_ami_id: str = "ami-0c02fb55956c7d316"  # Ubuntu 20.04 LTS
    key_pair_name: Optional[str] = None
    security_group_ids: list = []
    subnet_id: Optional[str] = None
    
    # Runner Configuration
    runner_labels: list = ["self-hosted", "linux", "x64"]
    max_runners: int = 10
    runner_idle_timeout: int = 300  # 5 minutes
    
    # Server Configuration
    webhook_port: int = 8080
    webhook_host: str = "0.0.0.0"
    debug: bool = False
    
    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            github_token=os.getenv("GITHUB_TOKEN", ""),
            github_org=os.getenv("GITHUB_ORG", ""),
            github_repo=os.getenv("GITHUB_REPO"),
            github_api_base=os.getenv("GITHUB_API_BASE", "https://api.github.com"),
            github_base_url=os.getenv("GITHUB_BASE_URL", "https://github.com"),
            
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            
            default_instance_type=os.getenv("DEFAULT_INSTANCE_TYPE", "t3.medium"),
            default_ami_id=os.getenv("DEFAULT_AMI_ID", "ami-0c02fb55956c7d316"),
            key_pair_name=os.getenv("KEY_PAIR_NAME"),
            security_group_ids=os.getenv("SECURITY_GROUP_IDS", "").split(",") if os.getenv("SECURITY_GROUP_IDS") else [],
            subnet_id=os.getenv("SUBNET_ID"),
            
            runner_labels=os.getenv("RUNNER_LABELS", "self-hosted,linux,x64").split(","),
            max_runners=int(os.getenv("MAX_RUNNERS", "10")),
            runner_idle_timeout=int(os.getenv("RUNNER_IDLE_TIMEOUT", "300")),
            
            webhook_port=int(os.getenv("WEBHOOK_PORT", "8080")),
            webhook_host=os.getenv("WEBHOOK_HOST", "0.0.0.0"),
            debug=os.getenv("DEBUG", "false").lower() == "true"
        ) 