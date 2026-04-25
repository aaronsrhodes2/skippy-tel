"""skippytel-insightface — Hey Buddy face recognition microservice.

Internal-only on the compose network. Loads InsightFace buffalo_l on
first request (detection + recognition + 512-dim embedding).

Privacy doctrine (from brief): face DB never leaves the PC. Embeddings
are stored in /data/faces.db (mounted volume on host). Raw photos are
NEVER persisted — we extract the embedding and discard the bytes.

Endpoints:
  GET  /health
  POST /identify     multipart image → strongest face match or null
  POST /enroll       multipart image + form name → {ok, embedding_id}
  GET  /enrolled     list distinct names enrolled
  DELETE /enrolled/{name}  remove all embeddings for a name
"""
import os
import sqlite3
import threading
from typing import Optional

import cv2
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from insightface.app import FaceAnalysis

DB_PATH = os.getenv("FACE_DB_PATH", "/data/faces.db")
MODEL_NAME = os.getenv("INSIGHTFACE_MODEL", "buffalo_l")
# Cosine-similarity threshold for "is this a match." 0.4 is the
# typical buffalo_l value; tune up for fewer false positives.
MATCH_THRESHOLD = float(os.getenv("MATCH_THRESHOLD", "0.40"))
DET_SIZE = int(os.getenv("DET_SIZE", "640"))

_apps: dict[int, FaceAnalysis] = {}
_apps_lock = threading.Lock()


def get_app(det_size: int = DET_SIZE) -> FaceAnalysis:
    """Lazy-load InsightFace per det_size. SCRFD anchors are baked at
    prepare() time and don't fully re-init when prepare() is called
    again on the same instance — so we keep one instance per size."""
    if det_size not in _apps:
        with _apps_lock:
            if det_size not in _apps:
                a = FaceAnalysis(
                    name=MODEL_NAME,
                    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
                )
                a.prepare(ctx_id=0, det_size=(det_size, det_size))
                _apps[det_size] = a
    return _apps[det_size]


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS faces (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            embedding   BLOB NOT NULL,
            enrolled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_name ON faces(name)")
    return conn


def _decode(image_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="could not decode image")
    return img


def _strongest_face(img: np.ndarray):
    """Try DET_SIZE (good for group photos / small faces) first. If
    nothing's found, retry at 320 (good for face-fills-frame selfies
    that overshoot SCRFD's anchor sizes at 640+)."""
    faces = get_app(DET_SIZE).get(img)
    if not faces and DET_SIZE != 320:
        faces = get_app(320).get(img)
    if not faces:
        return None
    return max(faces, key=lambda f: f.det_score)


app = FastAPI(title="skippytel-insightface")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "insightface",
        "model": MODEL_NAME,
        "loaded": _app is not None,
        "match_threshold": MATCH_THRESHOLD,
    }


@app.post("/identify")
async def identify(image: UploadFile = File(...)):
    img = _decode(await image.read())
    face = _strongest_face(img)
    if face is None:
        return {"match": None, "reason": "no_face_detected"}

    query = face.normed_embedding.astype(np.float32)

    # Pull all stored embeddings, compute cosine sim, return best.
    # Brute force is fine — single user, hundreds of friends max.
    best_name = None
    best_score = -1.0
    with _db() as conn:
        for name, blob in conn.execute("SELECT name, embedding FROM faces"):
            stored = np.frombuffer(blob, dtype=np.float32)
            score = float(np.dot(query, stored))
            if score > best_score:
                best_score = score
                best_name = name

    if best_name is None or best_score < MATCH_THRESHOLD:
        return {"match": None, "best_score": best_score,
                "threshold": MATCH_THRESHOLD}

    return {
        "match": {"name": best_name, "confidence": best_score},
        "threshold": MATCH_THRESHOLD,
    }


@app.post("/enroll")
async def enroll(image: UploadFile = File(...), name: str = Form(...)):
    if not name.strip():
        raise HTTPException(status_code=400, detail="name required")
    img = _decode(await image.read())
    face = _strongest_face(img)
    if face is None:
        raise HTTPException(status_code=400, detail="no face detected")
    emb = face.normed_embedding.astype(np.float32).tobytes()
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO faces (name, embedding) VALUES (?, ?)",
            (name.strip(), emb),
        )
        conn.commit()
        return {"ok": True, "embedding_id": cur.lastrowid, "name": name.strip()}


@app.get("/enrolled")
def enrolled():
    with _db() as conn:
        rows = conn.execute(
            "SELECT name, COUNT(*) as n FROM faces GROUP BY name ORDER BY name"
        ).fetchall()
    return {"enrolled": [{"name": n, "samples": c} for n, c in rows]}


@app.delete("/enrolled/{name}")
def remove(name: str):
    with _db() as conn:
        cur = conn.execute("DELETE FROM faces WHERE name = ?", (name,))
        conn.commit()
        return {"ok": True, "removed": cur.rowcount}
