import sys
sys.path.insert(0, '/app')
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

creds = Credentials.from_authorized_user_file('/drive-state/drive_token.json', ['https://www.googleapis.com/auth/drive'])
if creds.expired and creds.refresh_token:
    creds.refresh(Request())
svc = build('drive', 'v3', credentials=creds)

AUDIO_EXTS = (".mp3", ".m4a", ".flac", ".ogg", ".wav", ".aac", ".opus", ".wma")

def show_folder(folder_id, name, depth=0):
    indent = "  " * depth
    resp = svc.files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        pageSize=10,
        fields='files(id, name, mimeType)'
    ).execute()
    files = resp.get('files', [])
    audio = [f for f in files if f['mimeType'].startswith('audio/') or any(f['name'].lower().endswith(e) for e in AUDIO_EXTS)]
    folders = [f for f in files if f['mimeType'] == 'application/vnd.google-apps.folder']
    other = [f for f in files if f not in audio and f not in folders]
    print(f"{indent}[{name}]: {len(audio)} audio, {len(folders)} subfolders, {len(other)} other")
    if audio:
        for a in audio[:3]:
            print(f"{indent}  AUDIO: {a['name'][:60]}  ({a['mimeType']})")
    if other:
        for o in other[:2]:
            print(f"{indent}  OTHER: {o['name'][:40]}  ({o['mimeType']})")
    return audio, folders

# Check 5 folders from Music/ root
music_id = '1OVrFblkpAweuA4oUXOcyjqaRQkm6ZNbU'
resp = svc.files().list(
    q=f"'{music_id}' in parents and trashed = false",
    pageSize=10,
    fields='files(id, name, mimeType)'
).execute()

total_audio = 0
for f in resp.get('files', []):
    if f['mimeType'] == 'application/vnd.google-apps.folder':
        audio, subs = show_folder(f['id'], f['name'], depth=0)
        total_audio += len(audio)
        for sub in subs[:2]:
            a2, _ = show_folder(sub['id'], sub['name'], depth=1)
            total_audio += len(a2)
    elif f['mimeType'].startswith('audio/') or any(f['name'].lower().endswith(e) for e in AUDIO_EXTS):
        print(f"ROOT AUDIO: {f['name']} ({f['mimeType']})")
        total_audio += 1

print(f"\nTotal audio files spotted in sample: {total_audio}")
