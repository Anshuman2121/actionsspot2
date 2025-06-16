# GitHub Actions Self-Hosted Runner Manager

A Python application that automatically creates AWS spot instances for GitHub Actions self-hosted runners, similar to [RunsOn](https://runs-on.com/).

## Features

- 🚀 Automatic runner creation on workflow job events
- 💰 Cost-effective using AWS spot instances (up to 90% savings)
- ⚙️ Flexible configuration via workflow labels
- 🔄 Auto cleanup when jobs complete
- 🔒 Secure - runs in your AWS account

## Quick Start

1. **Install Dependencies**
```bash
pip install -r requirements.txt
```

2. **Configure Environment**
```bash
cp env.example .env
# Edit .env with your settings
```

3. **Required Environment Variables**
```bash
GITHUB_TOKEN=your_github_token
GITHUB_ORG=your-organization
SECURITY_GROUP_IDS=sg-xxxxxxxx
SUBNET_ID=subnet-xxxxxxxx
```

4. **Run the Application**
```bash
python main.py
```

The application will start polling GitHub's API for queued jobs automatically.

## Workflow Usage

```yaml
jobs:
  test:
    runs-on:
      - runs-on=${{ github.run_id }}
      - instanceType=t3.medium
    steps:
      - uses: actions/checkout@v3
      - run: echo "Running on spot instance!"
```

## Configuration Options

| Label | Description | Example |
|-------|-------------|---------|
| `runs-on=<id>` | Unique identifier | `runs-on=${{ github.run_id }}` |
| `instanceType=<type>` | EC2 instance type | `instanceType=c5.xlarge` |
| `cpu=<count>` | CPU count (auto-select instance) | `cpu=8` |
| `maxPrice=<price>` | Max spot price | `maxPrice=0.15` |

## AWS Permissions Required

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeInstances",
                "ec2:DescribeSpotInstanceRequests", 
                "ec2:RequestSpotInstances",
                "ec2:TerminateInstances",
                "ec2:CreateTags"
            ],
            "Resource": "*"
        }
    ]
}
```

## Architecture

The application polls GitHub's API for queued jobs, uses GitHub's internal APIs to create scale sets and JIT configurations, parses runner configuration from workflow labels, creates AWS spot instances, and automatically cleans up when jobs complete.

This approach uses the same internal APIs that GitHub Actions Runner Controller (ARC) uses, as documented in the [GitHub discussion](https://github.com/actions/actions-runner-controller/discussions/3289).

## Monitoring

- Health: `GET /health`
- Status: `GET /status`
- Manual cleanup: `POST /cleanup`

## License

MIT License - see the full application code for details. # actionsspot2
# actionsspot2
