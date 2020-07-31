import os.path
import pickle
from google.auth.transport.requests import Request
from google.oauth2 import service_account


SCOPES = [
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive',
]
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CREDENTIALS_FILE = os.path.join(BASE_DIR, 'secrets', 'google-credentials.json')


def get_creds():
    creds = None
    # The file token.pickle stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists('google_token.pickle'):
        with open('google_token.pickle', 'rb') as token:
            creds = pickle.load(token)

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds = service_account.Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
        # Save the credentials for the next run
        with open('google_token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    return creds