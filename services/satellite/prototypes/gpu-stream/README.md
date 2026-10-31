# GPU-streamed Skippy (prototype, not in the firmware)

Idea: skippy-pc draws Skippy's face (fast) and streams it to the room box, which only shows it. The box's own drawing tops out at ~5-10 frames/s.

What was measured (Office box, 2.4 GHz Wi-Fi, 480x480):
- ESPHome's stock `online_image` (fetch a JPEG per frame): ~230 ms per frame to download and decode, then it stalled: ~4 frames/s at best. Not better than drawing locally.
- UDP tiles (24x20 RGB565, only the changed ones) written straight to the panel by a custom component: the box takes only ~1,100 small packets/s whatever they hold,
  so ~8 frames/s for a can-sized moving block, ~15 frames/s for small changes. Packing compressed tiles (deflate, many per datagram, inflated with the S3 ROM's
  miniz) is the next step; `skippy_stream/` and `avatar_udp.py` are that version, built but NOT validated: the first run on the box crashed
  (LoadProhibited in the receiving task) when it was sent a bad stream, so don't flash it before the receiver is hardened (bounds-check records, never
  write when `active_` is false, test the compressed path with a known-good frame first).
- The renderer side (a Pillow port of the face, hat physics, leans) is not written; `avatar_stream.py` is only a test-pattern generator.

To resume: harden the receiver, validate the compressed protocol, then port the face drawing to Python (Pillow or GPU) and drive it from the hub's emotion/mouth tracks.
