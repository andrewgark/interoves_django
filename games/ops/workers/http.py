"""Shared checks for private Elastic Beanstalk worker HTTP delivery."""

from games.runtime import runtime_role


def sqsd_message_id(request):
    return request.headers.get('X-Aws-Sqsd-Msgid', '').strip()


def is_sqsd_delivery(request, *, role):
    """True when this process role matches and the caller is native sqsd.

    sqsd cannot attach an HMAC header.  The worker security group is the
    network boundary; the role check stops the public web process from
    accepting the same headers.
    """
    if runtime_role() != role:
        return False
    user_agent = request.headers.get('User-Agent', '')
    return bool(sqsd_message_id(request) and user_agent.lower().startswith('aws-sqsd'))
