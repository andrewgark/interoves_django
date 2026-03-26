#!/bin/bash
# Runs after the EB engine writes /opt/elasticbeanstalk/deployment/env from
# CF metadata (which has the stale plugins.rds.env password), and before
# systemd starts the application. Overwrites RDS_PASSWORD with the real
# value from Secrets Manager so Daphne gets the correct password.
set -e

python3 << 'PYEOF'
import subprocess, json

SECRET_ARN = 'arn:aws:secretsmanager:eu-central-1:916000456640:secret:rds!db-ce1a594a-9964-4a32-a9d3-9483ada5368c-0O6ead'
ENV_FILE = '/opt/elasticbeanstalk/deployment/env'

r = subprocess.run(
    ['aws', 'secretsmanager', 'get-secret-value',
     '--region', 'eu-central-1',
     '--secret-id', SECRET_ARN,
     '--query', 'SecretString', '--output', 'text'],
    capture_output=True, text=True, check=True
)
pw = json.loads(r.stdout)['password']
assert "'" not in pw, 'Password contains single quote - adjust quoting strategy'

with open(ENV_FILE) as f:
    lines = f.readlines()

found = False
new_lines = []
for line in lines:
    if line.startswith('RDS_PASSWORD='):
        new_lines.append("RDS_PASSWORD='{}'\n".format(pw))
        found = True
    else:
        new_lines.append(line)
if not found:
    new_lines.append("RDS_PASSWORD='{}'\n".format(pw))

with open(ENV_FILE, 'w') as f:
    f.writelines(new_lines)

print('predeploy: RDS_PASSWORD updated in', ENV_FILE)
PYEOF
