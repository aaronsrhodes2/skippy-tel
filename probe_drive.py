import sys
sys.path.insert(0, '/app')
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

creds = Credentials.from_authorized_user_file('/drive-state/drive_token.json', ['https://www.googleapis.com/auth/drive'])
if creds.expired and creds.refresh_token:
    creds.refresh(Request())
svc = build('drive', 'v3', credentials=creds)

folder_id = '1OVrFblkpAweuA4oUXOcyjqaRQkm6ZNbU'
resp = svc.files().list(
    q=f"'{folder_id}' in parents and trashed = false",
    pageSize=15,
    fields='files(id, name, mimeType)'
).execute()

for f in resp.get('files', []):
    print(f"{f['mimeType'][:45]:<47} {f['name'][:60]}")
print('---')
print('Total shown:', len(resp.get('files', [])))

# Also check one subfolder
for f in resp.get('files', []):
    if f['mimeType'] == 'application/vnd.google-apps.folder':
        resp2 = svc.files().list(
            q=f"'{f['id']}' in parents and trashed = false",
            pageSize=5,
            fields='files(id, name, mimeType)'
        ).execute()
        print(f"\nInside folder '{f['name']}':")
        for ff in resp2.get('files', []):
            print(f"  {ff['mimeType'][:45]:<47} {ff['name'][:60]}")
        break
