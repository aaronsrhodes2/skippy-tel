# Puppet mode: Skippy's face as a scene, not pixels (design, 2026-11-11)

## Why

The Captain (2026-11-11): the hub avatar looks pixelated and choppy when it works or draws a lot, and the avatar is the face of the house
(body language, emotion). The Wi-Fi is not going to change: the pods will sit in many rooms, so the stream has to cope with weak links.

**Measured** (`tools/face_probe.py`, the living pod, idle, 2026-11-11 20:21): signal -69 to -71 dBm; about 16% of datagrams never arrive
(52/s missing against 280/s received); the hub sends 10-20 frames/s and 4 to 9 whole frames/s arrive. The box is not the limit (a tile
unpacks in 18 us and draws in 16 us; no drops, no bad packets). The hub also halves its rate when it sees loss, which makes the stale
16x16 blocks (the "pixelation") heal in about 11 s instead of 6 s.

A pixel-tile stream cannot be made reliable on a lossy link: every lost datagram is a stale block until the rolling refresh reaches it,
and the stream costs 17-37 KB per frame.

## The idea (the Flash model)

Send what the face *is*, not what it looks like. `face_render.Face.render()` already draws the whole face from ~60-80 primitives
(anti-aliased polylines, filled/bordered ellipses and rectangles, a handful of RGBA images: the can in five lean poses, text lines).
Puppet mode sends that draw list; the box rasterizes it. Sprites (the can, text) go once and are cached on the box.

**Measured** (`tools/face_ops_probe.py`, same frames as the tile encoder, wire sizes in the script):

| state     | tile stream  | draw list, whole scene | draw list, changed ops only |
|-----------|--------------|------------------------|-----------------------------|
| idle      | 16.9 KB/frame | 1.9 KB                | 0.55 KB |
| listening | 21.8 KB      | 2.4 KB                 | 1.0 KB |
| recording | 24.5 KB      | 2.5 KB                 | 1.0 KB |
| thinking  | 24.8 KB      | 2.0 KB                 | 0.6 KB |
| speaking  | 36.5 KB      | 2.1 KB                 | 1.2 KB |

The **whole scene fits in two datagrams**. So there is no delta and no refresh: every frame carries the complete scene, a lost frame
costs one frame of pose (the next one repairs everything), and a block can never be stale. To get through 16% loss send each scene
twice (or one XOR-parity datagram): about 5 KB/frame, still 5-7 times less than today.

## Design

1. **Scene packet** (`'K', 3`): frame u16, then ops in painter's order; op kinds: polyline (npts, width, rgb, opa, rounded, i16 points),
   box/ellipse (x y w h, rgb, opa, radius, border), sprite (id, x, y, opa). The op list is the order `render()` draws, so ordering
   is by construction the same as today's face.
2. **Sprites**: the five can poses and cached text lines, RGB565 + alpha, in PSRAM, keyed by id/hash. The hub sends them on connect and again
   when the box's `Sprites` sensor (a hash) does not match; text lines are sent the first time they are used.
3. **Box compositor** (new component `skippy_puppet`, beside `skippy_stream`, drawing into the same panel frame buffer on vsync):
   keep the last scene; for each changed op take the union of its old and new bounding boxes; clear that rectangle to black and redraw, in
   order, every op that touches it. Rasterizers: AA capsule polyline (coverage from distance), AA ellipse, rect, sprite alpha blit.
   The box's LVGL cannot do this (it reaches 5 frames/s: `rooms/README.md`), so it is a small hand-written rasterizer.
4. **Tweening** (the Flash part): the hub sends scenes at 10-15 Hz; the box interpolates each op's numbers between the last two scenes
   and draws at the panel's pace. Choppiness stops being a function of the link.
5. **Fallback**: a box without the puppet firmware, or one that reports it cannot keep up, keeps the current tile stream (`FaceStreamer`
   decides by the box's `Stream mode` sensor). Nothing about today's path is removed until puppet mode has been seen on a real pod.

## Props, clips and updates everywhere (the Captain, 2026-11-11)

- **Props**: Skippy gets a library of props (the pencil and pad, the microphone, a hat, glasses, a lightbulb, a coffee cup, a wrench, a
  magnifier, a flag, whatever he needs for a bit). A prop is a sprite or a few primitives, so it is data: adding one is adding an entry to
  the library on the hub, not building firmware. A scene just lists it like any other op.
- **Clips**: pre-programmed animation sequences and body-language responses (nod, shrug, double take, lean in, wave, fall asleep, "aha"), each a
  keyframe timeline with easing, played by the box at its own frame rate between the hub's scenes, with seeded random variation (timing, amplitude,
  which of several variants) so he is never identical twice. The hub says "play `shrug`, seed 41" and the box does the rest, so smoothness no longer
  depends on the link. Use every frame the panel can show.
- **One avatar, everywhere, always**: the avatar lives only on the hub (sprite library + clip library + the scene logic in `face_render.py`).
  On connect the hub checks the box's library hash (a text sensor) against its own and sends what differs, so a new prop or a changed look reaches
  every pod the next time it is awake and connected, with no flashing. Firmware changes only when the rasterizer or the wire format does, and
  the hub refuses puppet mode for a pod whose firmware version is older than it needs and keeps that pod on tiles (and flashes it). This also
  ends the duplicate face in `skippy-room.yaml` (the LVGL fallback has to be kept in step by hand today): the fallback becomes a plain
  "hub unreachable" screen.

## Spike result (2026-11-11, the living pod, `tools/puppet_bench.py`)

The box's own rasterizer (`rooms/components/skippy_puppet`, a first naive version) drew the recorded idle scene (66 ops, 1,997 bytes + the can sprite)
and the screenshot matches the PC's render of the same frame (`.skippy-shots/puppet-compare.png`): **it looks right**. Differences: the nimbus
ring (drawn inline by `render()`, not through the three recorded calls: it has to become 16 ops), and 1 px of anti-aliasing at edges.

**Speed: not yet.** Whole scene 236 ms (lines 157, boxes 60, sprite 20; clearing the screen alone is 21 ms: the frame buffer is PSRAM); a 128x96 dirty
patch 27 ms (boxes 15, lines 8, sprite 3). The budget at 20-30 fps is 33-50 ms per frame. The first version does float math and a PSRAM
read-modify-write per pixel, so there is a lot of room; the plan to get there:
1. compose each dirty rectangle in internal RAM (a 128x96x2 scratch = 24 KB) and copy it to the panel once, so PSRAM sees one write, not many reads;
2. coverage from small tables (squared distance -> 0..255) instead of `sqrtf`, integer blending, skip a polyline's untouched pixels by scanline spans;
3. merge dirty rectangles per frame from what actually moved (a blink and a mouth move small areas); redraw only ops that touch them;
4. opaque boxes (`opa == 255`, most of them) skip the read entirely, and a box fully covered by a later opaque box is dropped by the hub.
Go/no-go: if the typical frame's dirty area redraws in under ~15 ms after 1-3, puppet mode is a go. Until then the tile stream stays.

## Spike result, round 2 (after the first optimization: internal-RAM strips, no sqrt for interior pixels, sprite row spans)

Whole scene 236 -> 140 ms; a 128x96 patch 27 -> 15.6 ms (about 1.27 us per pixel). The slow ops left are the three 41-point hat polylines
(28 + 16 + 14 ms: they span 500x300 px) and the can sprite (12 ms).

**But the face changes a lot every frame** (`tools/face_dirty_probe.py`, the real animation): per frame the changed 16 px tiles cover, on average,
29k px at idle, 38k listening, 42k recording, 45k thinking, 66k speaking (p95 94-113k px). At 1.27 us/px that is 37 / 48 / 53 / 57 / 84 ms mean
per frame (p95 120-140 ms), even with perfect dirty rectangles, and 90-160 ms with per-op boxes (the hat's outlines move as whole shapes). So the box
becomes the bottleneck: puppet mode as built would run about 10-25 fps, no better than the tile stream's nominal rate, though with no stale blocks.

**Verdict:** the bandwidth win is real (2 KB a frame, loss-proof) but not a speed win until drawing is several times cheaper. Paths that could
get there: (a) use both ESP32-S3 cores on strips (~2x); (b) integer/fixed-point coverage (~2x); (c) animate big shapes by moving pixels instead
of redrawing them (the hat swing as a sprite shift); (d) lower the face's own churn (sub-pixel sway that changes no visible pixel but changes ops).
Not started: the Captain redirected to the music UI (2026-11-11). **The cheaper fix for the pixelation is on the existing tile stream**
(option 3 in the first analysis: on loss keep the frame rate and resend exactly the lost tiles, instead of halving the rate), and puppet mode
stays parked here with the spike firmware on the living pod (opt-in; the tile stream is untouched).

## What is proven and what is not

- Proven: the face is ~60-80 primitives, the scene is 2-2.5 KB, and the tile stream costs 8-17 times more. (numbers above)
- Not proven: that the ESP32-S3 can rasterize those ops at 20+ fps (the can sprite is the big blit; dirty rectangles keep it small, but it
  has not been measured). Step 1 below is a spike that answers it before anything else is built.
- Colour banding (RGB565 on slow gradients such as the deepening orange) is a separate, small fix: dither on the PC, or choose colours that
  are exact in 565. Puppet mode does not change it.

## Order of work

1. Op recorder in `face_render.py` (today's `tools/face_ops_probe.py` is its prototype) + `puppet.py` encoder + a Python reference
   rasterizer-for-the-box that is compared with `Face.render()` (pixel difference, anti-aliasing tolerances).
2. `skippy_puppet` component: a spike that draws one static recorded scene on the pod and reports its draw time per frame. Go/no-go on speed.
3. Sprite upload + hash handshake; scene stream; loss strategy (double send); the `Stream mode` sensor.
4. Tweening on the box; then new body language (it is cheap now: a gesture is a few numbers, not pixels).
5. Flash the pods; keep tiles as the fallback for a release, then retire them.

Tools: `tools/face_probe.py` (the box's own counters, signal and face state, to `state/face-probe.log`), `tools/face_ops_probe.py`.
