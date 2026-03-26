#!/bin/bash
# Ensure the SSM agent is running so `aws ssm send-command` works from any
# machine with the right IAM permissions (no SSH keys or SG changes needed).
systemctl enable amazon-ssm-agent --now 2>&1 || true
