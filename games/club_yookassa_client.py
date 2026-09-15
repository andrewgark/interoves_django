"""Bounded, single-attempt HTTP calls for Club billing under a database lock."""
import requests
from yookassa import Payment
from yookassa.client import ApiClient


class ClubApiClient(ApiClient):
    def execute(self, body, method, path, query_params, request_headers):
        # The installed SDK's Configuration.timeout controls retry backoff, NOT
        # socket timeout. Its default execute() can wait indefinitely. A timeout
        # here remains an ambiguous payment, reconciled by the verified webhook.
        with requests.Session() as session:
            return session.request(
                method, self.endpoint + path, params=query_params,
                headers=request_headers, json=body, timeout=(3.05, 10),
            )


class ClubPayment(Payment):
    def __init__(self):
        self.client = ClubApiClient()
