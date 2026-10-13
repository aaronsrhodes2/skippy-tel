# Orion Trail (vendored from its own repo; edit it THERE, then re-vendor)

From https://github.com/aaronsrhodes2/orion-trail at commit `8bf1ae6725f0104290893c707a6f9fe5119ed048`: `orion-trail.html` and `scenarios/*.js`
(the repo's `tools/` and `misc/` aren't needed to play). The portal page `templates/orion_trail.html` frames it under
the portal's app bar, like Weapon vs. Armor's page. Saves stay in the player's own browser (localStorage).

To update after a change in the game's repo:

```bash
bash tools/vendor_orion_trail.sh
```
