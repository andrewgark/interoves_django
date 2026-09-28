from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class RunWorkerCommandTests(SimpleTestCase):
    def test_missing_queue_url_fails_before_creating_aws_client(self):
        output = StringIO()
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(CommandError), patch('boto3.client') as client:
            call_command(
                'run_worker', '--worker', 'background', '--mode', 'ecs-fargate', '--once',
                stdout=output, stderr=output,
            )
        client.assert_not_called()
