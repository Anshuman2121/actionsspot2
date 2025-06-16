#!/usr/bin/env python3
"""
GitHub Actions Self-Hosted Runner Manager

A Python application that automatically creates AWS spot instances for GitHub Actions 
self-hosted runners by polling GitHub's API for queued jobs.

Usage:
    python main.py

Environment Variables:
    GITHUB_TOKEN - GitHub personal access token
    GITHUB_ORG - GitHub organization name (required)
    AWS_REGION - AWS region (default: us-east-1)
    DEFAULT_INSTANCE_TYPE - Default EC2 instance type (default: t3.medium)
    SECURITY_GROUP_IDS - Comma-separated security group IDs
    SUBNET_ID - VPC subnet ID for instances
    POLL_INTERVAL - Polling interval in seconds (default: 30)
"""

import os
import sys
import logging
import signal
import time
from dotenv import load_dotenv
from config import Config
from job_poller import JobPoller
from flask import Flask, jsonify


def setup_logging(debug: bool = False):
    """Setup application logging"""
    level = logging.DEBUG if debug else logging.INFO
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Setup console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    
    # Setup file handler
    file_handler = logging.FileHandler('runner-manager.log')
    file_handler.setFormatter(formatter)
    
    # Setup root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # Reduce noise from boto3 and requests
    logging.getLogger('boto3').setLevel(logging.WARNING)
    logging.getLogger('botocore').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)


def validate_config(config: Config) -> bool:
    """Validate required configuration"""
    required_fields = [
        ('github_token', 'GITHUB_TOKEN'),
        ('github_org', 'GITHUB_ORG')
    ]
    
    missing_fields = []
    for field, env_var in required_fields:
        if not getattr(config, field):
            missing_fields.append(env_var)
    
    if missing_fields:
        logging.error(f"Missing required environment variables: {', '.join(missing_fields)}")
        return False
    
    return True


def create_api_server(poller: JobPoller) -> Flask:
    """Create a simple API server for monitoring"""
    app = Flask(__name__)
    
    @app.route('/health', methods=['GET'])
    def health():
        return jsonify({'status': 'healthy', 'service': 'github-runner-manager'})
    
    @app.route('/status', methods=['GET'])
    def status():
        return jsonify(poller.get_status())
    
    @app.route('/cleanup', methods=['POST'])
    def manual_cleanup():
        """Manual cleanup endpoint for testing"""
        try:
            cleaned = poller.aws.cleanup_old_instances(max_age_hours=0)
            return jsonify({'cleaned_instances': cleaned})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    return app


def main():
    """Main application entry point"""
    # Load environment variables from .env file if it exists
    load_dotenv()
    
    # Load configuration
    config = Config.from_env()
    
    # Setup logging
    setup_logging(config.debug)
    logger = logging.getLogger(__name__)
    
    # Validate configuration
    if not validate_config(config):
        logger.error("Configuration validation failed")
        sys.exit(1)
    
    logger.info("Starting GitHub Actions Runner Manager (API-based)")
    logger.info(f"GitHub Organization: {config.github_org}")
    logger.info(f"AWS Region: {config.aws_region}")
    logger.info(f"Default Instance Type: {config.default_instance_type}")
    logger.info(f"Max Runners: {config.max_runners}")
    
    # Create job poller
    poller = JobPoller(config)
    
    # Start polling
    poll_interval = int(os.getenv('POLL_INTERVAL', '30'))
    poller.start_polling(config.github_org, poll_interval)
    
    # Create API server for monitoring
    app = create_api_server(poller)
    
    try:
        # Setup signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            logger.info("Received shutdown signal, cleaning up...")
            poller.stop_polling()
            sys.exit(0)
        
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        
        # Start the API server
        logger.info(f"Starting API server on port {config.webhook_port}")
        app.run(
            host=config.webhook_host,
            port=config.webhook_port,
            debug=config.debug
        )
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
        poller.stop_polling()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        poller.stop_polling()
        sys.exit(1)


if __name__ == '__main__':
    main() 