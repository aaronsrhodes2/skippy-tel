# Orion Trail (vendored from its own repo; edit it THERE, then re-vendor)

From https://github.com/aaronsrhodes2/orion-trail at commit `4edeb4f4665e18c7b331f3ca591e11bf78dd6396`: `orion-trail.html` and `scenarios/*.js`
(the repo's `tools/` and `misc/` aren't needed to play). The portal page `templates/orion_trail.html` frames it under
the portal's app bar, like Weapon vs. Armor's page. Saves stay in the player's own browser (localStorage).

To update after a change in the game's repo:

```bash
bash tools/vendor_orion_trail.sh
```
