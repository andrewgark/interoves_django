"""Generic SQS-to-Django adapter for ECS and supervised processes.

This is deliberately a transitional adapter: it invokes the existing private
worker view, so the first ECS/Green deployment does not duplicate domain logic.
The views can later be reduced to adapters over the same dispatcher without
changing this polling contract.
"""

from __future__ import annotations

import logging
import os
import threading

from django.db import close_old_connections
from django.test import RequestFactory

from games.worker_contract import WORKER_REGISTRY, WorkerSpec

logger = logging.getLogger('application')

VISIBILITY_HEARTBEAT_INTERVAL = 60
VISIBILITY_EXTENSION_SECONDS = 1800


VIEW_BY_WORKER = {
    'background': 'games.views.background_worker.background_worker',
    'identity': 'games.views.identity_worker.identity_worker',
    'integrations': 'games.views.integrations_worker.integrations_worker',
    'recheck': 'games.views.word_salad_worker.recheck_worker',
}


def queue_url_for(spec: WorkerSpec, environ=None):
    environ = os.environ if environ is None else environ
    return (
        (environ.get(spec.queue_url_env) or '').strip()
        or (environ.get('WORKER_QUEUE_URL') or '').strip()
    )


def poll_once(*, worker_name, client, queue_url, wait_seconds=20, visibility_timeout=None):
    """Receive and process at most one message; return a result summary."""
    spec = WORKER_REGISTRY.get(worker_name)
    kwargs = {
        'QueueUrl': queue_url,
        'MaxNumberOfMessages': 1,
        'WaitTimeSeconds': max(0, min(20, int(wait_seconds))),
        'AttributeNames': ['ApproximateReceiveCount', 'SentTimestamp'],
        'MessageAttributeNames': ['All'],
    }
    if visibility_timeout is not None:
        kwargs['VisibilityTimeout'] = max(0, int(visibility_timeout))
    response = client.receive_message(**kwargs)
    messages = response.get('Messages') or []
    if not messages:
        return {'status': 'empty'}

    message = messages[0]
    if spec.name == 'identity' and visibility_timeout:
        with _SqsVisibilityHeartbeat(
            client,
            queue_url,
            message['ReceiptHandle'],
            max(1, int(visibility_timeout)),
        ):
            status_code = _deliver(spec, message)
    else:
        status_code = _deliver(spec, message)
    receipt = message['ReceiptHandle']
    if 200 <= status_code < 300 or (400 <= status_code < 500 and status_code != 409):
        client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
        action = 'ack' if status_code < 300 else 'drop'
    else:
        action = 'retry'
    return {
        'status': action,
        'message_id': message.get('MessageId', ''),
        'http_status': status_code,
    }


class _SqsVisibilityHeartbeat:
    """Keep a long-running worker message hidden until its handler returns."""

    def __init__(self, client, queue_url, receipt_handle, visibility_timeout):
        self.client = client
        self.queue_url = queue_url
        self.receipt_handle = receipt_handle
        self.visibility_timeout = min(
            VISIBILITY_EXTENSION_SECONDS,
            max(1, int(visibility_timeout)),
        )
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name='sqs-visibility-heartbeat',
            daemon=True,
        )

    def _run(self):
        while not self.stop_event.wait(VISIBILITY_HEARTBEAT_INTERVAL):
            try:
                self.client.change_message_visibility(
                    QueueUrl=self.queue_url,
                    ReceiptHandle=self.receipt_handle,
                    VisibilityTimeout=self.visibility_timeout,
                )
            except Exception:
                logger.exception(
                    'sqs visibility heartbeat failed queue=%s receipt=%s',
                    self.queue_url, self.receipt_handle,
                )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.stop_event.set()
        self.thread.join(timeout=5)


def _deliver(spec, message):
    view = _resolve_view(spec.name)
    headers = {
        'HTTP_USER_AGENT': 'aws-sqsd/compat-worker-adapter',
        'HTTP_X_AWS_SQSD_MSGID': message.get('MessageId', ''),
    }
    close_old_connections()
    try:
        request = RequestFactory().post(
            spec.endpoint,
            data=message.get('Body', ''),
            content_type='application/json',
            **headers,
        )
        response = view(request)
        return int(response.status_code)
    except Exception:
        logger.exception(
            'worker polling adapter failed worker=%s message_id=%s',
            spec.name, message.get('MessageId', ''),
        )
        return 500
    finally:
        close_old_connections()


def _resolve_view(worker_name):
    dotted_path = VIEW_BY_WORKER[worker_name]
    module_name, function_name = dotted_path.rsplit('.', 1)
    module = __import__(module_name, fromlist=[function_name])
    return getattr(module, function_name)
