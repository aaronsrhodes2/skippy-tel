"""
GET /bilby/bigcard — public DJ Big Card display.

A purpose-built, self-contained HTML page for live shows.
No authentication. No Tailscale. Just a URL.

What it shows:
  - Artist name (large, green)
  - Track title (white)
  - BPM + key if available (amber)
  - Karaoke line with progress bar
  - QR code linking back to itself so the crowd can scan and follow along

How it works:
  - Page is served once (static HTML + inline JS)
  - JS polls /bilby/status + /bilby/karaoke every 2 seconds
  - Works on any phone browser, no app install, no login

QR code URL:
  Auto-detected from the request Host header — so when accessed via a
  Cloudflare tunnel or any public proxy, the QR code encodes the correct
  public URL automatically. Override with ?url=<public_url>.

Data sources (transparent to the page — it just polls SkippyTel):
  - Mac Bilby (Traktor): when BILBY_MAC_URL is set and Mac is reachable
  - Drive mode: when BILBY_DRIVE_FOLDER_ID is set
  - "No music playing" state: when both are absent or nothing is active

Setup — expose to the internet:
  Option A — Cloudflare Tunnel (recommended, free, no account needed):
    cloudflared tunnel --url http://localhost:3003
    → prints a public HTTPS URL, e.g. https://abc123.trycloudflare.com
    → crowd opens https://abc123.trycloudflare.com/bilby/bigcard
    → the QR code on the page already encodes that URL

  Option B — Tailscale Funnel (if Tailscale is installed):
    tailscale funnel 3003
    → crowd opens https://skippy-pc.tail<id>.ts.net/bilby/bigcard

  Option C — router port-forward (static IP or DDNS):
    forward external :3003 → this PC :3003
    → use ?url=http://<your-public-ip>:3003/bilby/bigcard to set QR target
"""
import base64
import io

import qrcode
import qrcode.image.pil
from flask import Blueprint, Response, request

bp = Blueprint("bilby_bigcard", __name__)

# ── Palette (matches Skippy / SkippyDroid visual grammar) ────────────────────
_C_BG          = "#000000"
_C_GREEN       = "#00ff88"
_C_DIM_GREEN   = "#003322"
_C_DIM_GREEN_HI= "#006644"
_C_WHITE       = "#ffffff"
_C_AMBER       = "#ffaa00"
_C_RED         = "#ff4444"
_C_DIM_BG      = "#001a0d"

# ── HTML template (inline CSS + JS — no external dependencies) ───────────────
_PAGE_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="theme-color" content="{bg}">
<title>♪ Now Playing</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}}
html,body{{
  height:100%;background:{bg};color:{green};
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  overscroll-behavior:none;
}}
body{{
  display:flex;flex-direction:column;align-items:center;
  justify-content:space-between;min-height:100vh;
  padding:env(safe-area-inset-top,16px) 20px env(safe-area-inset-bottom,16px);
}}

/* Status */
#status{{
  font-size:11px;letter-spacing:.08em;color:{dim_hi};
  margin-top:8px;align-self:flex-end;
}}
#status.live{{color:{dim_hi}}}
#status.off{{color:{red}}}

/* Main card */
#card{{
  flex:1;display:flex;flex-direction:column;align-items:center;
  justify-content:center;width:100%;max-width:640px;
  padding:16px 0;gap:6px;
}}
#artist{{
  font-size:clamp(28px,7vw,56px);font-weight:700;
  color:{green};text-align:center;line-height:1.1;
  word-break:break-word;
}}
#title{{
  font-size:clamp(18px,4.5vw,36px);font-weight:400;
  color:{white};text-align:center;line-height:1.2;
  word-break:break-word;margin-top:4px;
}}
#meta{{
  display:flex;gap:10px;margin-top:10px;flex-wrap:wrap;justify-content:center;
}}
.pill{{
  background:{dim_bg};border:1px solid {dim_green};
  padding:3px 12px;border-radius:3px;
  font-size:13px;color:{amber};letter-spacing:.04em;
}}

/* Karaoke */
#lyric{{
  font-size:clamp(14px,3.8vw,22px);
  color:{green};font-style:italic;text-align:center;
  min-height:1.5em;line-height:1.5;margin-top:12px;
  width:100%;transition:opacity .3s;
}}
#lyric.blank{{opacity:.2}}
#bar-bg{{
  width:100%;height:2px;background:{dim_green};border-radius:1px;
  margin-top:6px;
}}
#bar{{
  height:2px;background:{dim_hi};border-radius:1px;
  width:0%;transition:width .5s linear;
}}

/* No-music state */
#empty{{
  color:{dim_green};font-size:16px;text-align:center;
  display:none;line-height:1.6;
}}

/* QR */
#qr-section{{
  display:flex;flex-direction:column;align-items:center;
  gap:6px;padding-bottom:8px;
}}
#qr-label{{font-size:11px;color:{dim_green};letter-spacing:.06em;text-transform:uppercase}}
#qr-img{{width:120px;height:120px;image-rendering:pixelated}}
#qr-url{{
  font-size:10px;color:{dim_green};word-break:break-all;
  text-align:center;max-width:260px;
}}
</style>
</head>
<body>
<div id="status">● connecting…</div>

<div id="card">
  <div id="artist">—</div>
  <div id="title">—</div>
  <div id="meta">
    <div class="pill" id="bpm" style="display:none">— BPM</div>
    <div class="pill" id="key" style="display:none">—</div>
  </div>
  <div id="lyric" class="blank">♪</div>
  <div id="bar-bg"><div id="bar"></div></div>
  <div id="empty">Bilby is taking a long stoner nap…<br><span style="font-size:12px;color:{dim_green}">check back when the show starts</span></div>
</div>

<div id="qr-section">
  <div id="qr-label">scan to follow along</div>
  <img id="qr-img" src="{qr_src}" alt="QR code">
  <div id="qr-url">{public_url}</div>
</div>

<script>
(function(){{
  var last_lyric = '';

  function setText(id, val){{
    var el = document.getElementById(id);
    if (el && el.textContent !== val) el.textContent = val;
  }}

  async function poll(){{
    try {{
      var [sr, kr] = await Promise.all([
        fetch('/bilby/status'),
        fetch('/bilby/karaoke'),
      ]);
      var s  = await sr.json();
      var k  = await kr.json();
      var np = s.now_playing;
      var playing = np && s.source !== 'none';

      var card  = document.getElementById('card');
      var empty = document.getElementById('empty');
      var stat  = document.getElementById('status');

      // Hide/show states
      ['artist','title','meta','lyric','bar-bg'].forEach(function(id){{
        var el = document.getElementById(id);
        if (el) el.style.display = playing ? '' : 'none';
      }});
      empty.style.display = playing ? 'none' : '';

      if (playing) {{
        setText('artist', np.artist || '—');
        setText('title',  np.title  || '—');

        // Optional BPM / key (present when Drive/music session provides them)
        var bpmEl = document.getElementById('bpm');
        var keyEl = document.getElementById('key');
        if (np.bpm) {{
          bpmEl.textContent = Math.round(np.bpm) + ' BPM';
          bpmEl.style.display = '';
        }} else {{ bpmEl.style.display = 'none'; }}
        if (np.key) {{
          keyEl.textContent = np.key;
          keyEl.style.display = '';
        }} else {{ keyEl.style.display = 'none'; }}

        // Karaoke
        var line = k.line || '';
        var lyric = document.getElementById('lyric');
        if (line !== last_lyric) {{
          lyric.textContent = line || '♪';
          lyric.className = line ? '' : 'blank';
          last_lyric = line;
        }}
        var pct = Math.min(1, Math.max(0, k.elapsed_pct || 0));
        document.getElementById('bar').style.width = (pct * 100).toFixed(1) + '%';
      }}

      var src = s.source || 'none';
      var srcLabel = src === 'traktor' ? '● live · traktor'
                   : src === 'pushed'  ? '▲ cached · ' + (s.stale_s || 0) + 's ago'
                   : src === 'drive'   ? '● live · drive'
                   : '○ waiting…';
      stat.textContent = playing ? srcLabel : '○ waiting…';
      stat.className = playing ? 'live' : '';
    }} catch(e) {{
      document.getElementById('status').textContent = '● offline';
      document.getElementById('status').className = 'off';
    }}
  }}

  poll();
  setInterval(poll, 2000);
}})();
</script>
</body>
</html>
"""


def _make_qr_b64(url: str) -> str:
    """Generate a QR code for `url` and return it as a base64 PNG data-URL."""
    img = qrcode.make(
        url,
        image_factory=qrcode.image.pil.PilImage,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2,
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def _public_url() -> str:
    """
    Derive the public-facing URL for this page.

    Priority:
      1. ?url= query param (explicit override — useful when behind a reverse
         proxy that doesn't forward X-Forwarded-* headers correctly)
      2. X-Forwarded-Proto + Host headers (set by cloudflared and most proxies)
      3. Flask request.url (falls back to http://localhost:3003/bilby/bigcard
         when running locally — still correct for a local iPad on the same wifi)
    """
    override = request.args.get("url", "").strip()
    if override:
        return override

    host  = request.headers.get("X-Forwarded-Host") or request.host
    proto = request.headers.get("X-Forwarded-Proto") or request.scheme
    return f"{proto}://{host}/bilby/bigcard"


@bp.get("/bilby/bigcard")
def bilby_bigcard():
    """
    The DJ Big Card — audience display for live shows.

    Accessible to anyone with the URL. No login. No Tailscale.
    Just open it on an iPad, project it, and put the QR code on screen.
    """
    url    = _public_url()
    qr_src = _make_qr_b64(url)

    html = _PAGE_TEMPLATE.format(
        # palette
        bg       = _C_BG,
        green    = _C_GREEN,
        dim_green= _C_DIM_GREEN,
        dim_hi   = _C_DIM_GREEN_HI,
        white    = _C_WHITE,
        amber    = _C_AMBER,
        red      = _C_RED,
        dim_bg   = _C_DIM_BG,
        # content
        qr_src    = qr_src,
        public_url= url,
    )
    return Response(html, status=200, content_type="text/html; charset=utf-8")
