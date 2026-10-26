import os


RUNTIME_ROLE_WEB = 'web'
RUNTIME_ROLE_WORKER = 'worker'
RUNTIME_ROLE_BACKGROUND = 'background-worker'
RUNTIME_ROLE_INTEGRATION = 'integration-worker'
RUNTIME_ROLE_IDENTITY = 'identity-worker'


def runtime_role():
    return os.environ.get('INTEROVES_RUNTIME_ROLE', 'legacy').strip().lower() or 'legacy'


def deployment_environment():
    """Return the explicitly configured deployment environment label.

    EB does not expose its environment name to the application reliably.  Keep
    this value explicit so alerts cannot mistake a rollback environment for
    the public production environment.
    """
    return (
        os.environ.get('INTEROVES_ENVIRONMENT')
        or os.environ.get('INTEROVES_DEPLOYMENT_ENVIRONMENT')
        or 'unknown'
    ).strip().lower() or 'unknown'


def heavy_processing_allowed():
    return runtime_role() != RUNTIME_ROLE_WEB
