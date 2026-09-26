import os


RUNTIME_ROLE_WEB = 'web'
RUNTIME_ROLE_WORKER = 'worker'
RUNTIME_ROLE_BACKGROUND = 'background-worker'
RUNTIME_ROLE_INTEGRATION = 'integration-worker'
RUNTIME_ROLE_IDENTITY = 'identity-worker'


def runtime_role():
    return os.environ.get('INTEROVES_RUNTIME_ROLE', 'legacy').strip().lower() or 'legacy'


def heavy_processing_allowed():
    return runtime_role() != RUNTIME_ROLE_WEB
