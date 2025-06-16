import hashlib
import hmac
import json
import logging
from flask import Flask, request, jsonify
from config import Config
from runner_manager import RunnerManager


class WebhookServer:
    def __init__(self, config: Config):
        self.config = config
        self.runner_manager = RunnerManager(config)
        self.logger = logging.getLogger(__name__)
        
        # Create Flask app
        self.app = Flask(__name__)
        self.app.logger.setLevel(logging.INFO)
        
        # Register routes
        self._register_routes()

    def _register_routes(self):
        """Register Flask routes"""
        @self.app.route('/webhook', methods=['POST'])
        def webhook():
            return self._handle_webhook()
        
        @self.app.route('/health', methods=['GET'])
        def health():
            return jsonify({'status': 'healthy', 'service': 'github-runner-manager'})
        
        @self.app.route('/status', methods=['GET'])
        def status():
            return jsonify(self.runner_manager.get_status())
        
        @self.app.route('/cleanup', methods=['POST'])
        def manual_cleanup():
            """Manual cleanup endpoint for testing"""
            try:
                cleaned = self.runner_manager.aws.cleanup_old_instances(max_age_hours=0)
                return jsonify({'cleaned_instances': cleaned})
            except Exception as e:
                return jsonify({'error': str(e)}), 500

    def _handle_webhook(self):
        """Handle GitHub webhook events"""
        try:
            # Verify webhook signature
            if not self._verify_signature(request.data, request.headers.get('X-Hub-Signature-256')):
                self.logger.warning("Invalid webhook signature")
                return jsonify({'error': 'Invalid signature'}), 401
            
            # Parse payload
            payload = request.get_json()
            if not payload:
                return jsonify({'error': 'Invalid JSON payload'}), 400
            
            event_type = request.headers.get('X-GitHub-Event')
            action = payload.get('action')
            
            self.logger.info(f"Received webhook: {event_type} - {action}")
            
            # Handle workflow_job events
            if event_type == 'workflow_job':
                return self._handle_workflow_job_event(payload, action)
            
            # Ignore other events
            return jsonify({'message': 'Event ignored'}), 200
            
        except Exception as e:
            self.logger.error(f"Error handling webhook: {e}")
            return jsonify({'error': 'Internal server error'}), 500

    def _handle_workflow_job_event(self, payload, action):
        """Handle workflow_job events"""
        try:
            if action == 'queued':
                success = self.runner_manager.handle_workflow_job_queued(payload)
                if success:
                    return jsonify({'message': 'Runner creation initiated'}), 200
                else:
                    return jsonify({'message': 'Runner creation not needed or failed'}), 200
            
            elif action in ['completed', 'cancelled']:
                success = self.runner_manager.handle_workflow_job_completed(payload)
                return jsonify({'message': 'Job completion handled'}), 200
            
            else:
                return jsonify({'message': f'Action {action} ignored'}), 200
                
        except Exception as e:
            self.logger.error(f"Error handling workflow job event: {e}")
            return jsonify({'error': 'Failed to handle workflow job event'}), 500

    def _verify_signature(self, payload_body, signature_header):
        """Verify GitHub webhook signature"""
        if not signature_header or not self.config.github_webhook_secret:
            return False
        
        try:
            hash_object = hmac.new(
                self.config.github_webhook_secret.encode('utf-8'),
                msg=payload_body,
                digestmod=hashlib.sha256
            )
            expected_signature = "sha256=" + hash_object.hexdigest()
            return hmac.compare_digest(expected_signature, signature_header)
        except Exception as e:
            self.logger.error(f"Error verifying signature: {e}")
            return False

    def run(self):
        """Start the webhook server"""
        self.logger.info(f"Starting webhook server on {self.config.webhook_host}:{self.config.webhook_port}")
        self.app.run(
            host=self.config.webhook_host,
            port=self.config.webhook_port,
            debug=self.config.debug
        ) 