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
