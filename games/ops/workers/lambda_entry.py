"""AWS Lambda SQS adapter for the common worker contract.

The first supported Lambda worker is ``background``.  The adapter deliberately
reuses the same private view as EB and the SQS polling adapter until the domain
handlers are fully separated from HTTP.  Failed records are returned using
Lambda's partial-batch response contract; poison messages are acknowledged.
"""

from __future__ import annotations

from games.worker_contract import WORKER_REGISTRY
from games.worker_polling import _deliver


def handle_sqs_event(event, context=None, *, worker_name=None):
    records = event.get('Records') or []
    if worker_name is None:
        worker_name = _worker_name_from_event(event)
    spec = WORKER_REGISTRY.get(worker_name)
    if 'lambda' not in spec.allowed_modes:
        raise ValueError('worker {} does not support lambda mode'.format(worker_name))

    failures = []
    results = []
    for record in records:
        message_id = str(record.get('messageId') or record.get('eventID') or '')
        message = {
            'MessageId': message_id,
            'Body': record.get('body', ''),
        }
        status_code = _deliver(spec, message)
        retry = status_code == 409 or status_code >= 500
        if retry:
            failures.append({'itemIdentifier': message_id})
        results.append({
            'message_id': message_id,
            'http_status': status_code,
            'action': 'retry' if retry else ('ack' if status_code < 400 else 'drop'),
        })
    return {'batchItemFailures': failures, 'results': results}


def _worker_name_from_event(event):
    attributes = event.get('eventSourceARN') or ''
    for spec in WORKER_REGISTRY.all():
        if spec.queue_name in attributes:
            return spec.name
    raise ValueError('worker name is required when the queue ARN is not recognized')
