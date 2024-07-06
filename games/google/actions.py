import os
from string import Template
from googleapiclient.discovery import build
from games.google.creds import get_creds


GOOGLE_DIR = os.path.dirname(os.path.abspath(__file__))
GAMES_FOLDER_ID = '1q4Pi0kYCDXCJINHReMuF6SXoV06YX0HH'
GAMES_NOT_RELEASED_FOLDER_ID = '1QaOde9JJPWTMKRGOXieboZtWQFRoCvYA'
GAMES_RELEASED_FOLDER_ID = '1Jd9YikkZVE9FhpEYCgc3pJf5BpqHardo'


def create_doc(game, with_answers, docs_service, drive_service):
    title = game.name
    if with_answers:
        title += " [с ответами]"

    request_body = {
        'title': title,
    }

    doc = docs_service.documents().create(body=request_body).execute()
    doc_id = doc['documentId']

    requests = []
    requests.append({'insert_text': {'text': game.name + '\n', 'location': {'index': 1}}})
    requests.append({'insert_text': {'text': game.author + '\n', 'location': {'index': 1}}})

    docs_service.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()
    
    return doc


def move_to_folder(doc_id, folder_id, docs_service, drive_service):
    doc = drive_service.files().get(fileId=doc_id,
                                    fields='parents').execute();
    previous_parents = ",".join(doc.get('parents'))

    drive_service.files().update(fileId=doc_id,
                                 addParents=folder_id,
                                 removeParents=previous_parents,
                                 fields='id, parents').execute()


def create_google_doc(game, with_answers=False):
    creds = get_creds()

    docs_service = build('docs', 'v1', credentials=creds)
    drive_service = build('drive', 'v3', credentials=creds)
    
    # print('https://docs.google.com/document/d/{}/edit#'.format(doc['documentId']))
    
    doc = create_doc(game, with_answers, docs_service, drive_service)
    doc_id = doc['documentId']

    move_to_folder(doc_id, GAMES_NOT_RELEASED_FOLDER_ID, docs_service, drive_service)
