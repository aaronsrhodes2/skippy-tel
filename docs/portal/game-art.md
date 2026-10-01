# Game page art: where each image came from

Each game's page has two images from portal/static/games, wired in `portal/catalog.py` (`Theme.bg_image`, `Theme.hero_image`,
`Theme.hero_focus`) and rendered by `portal/templates/game.html`:

* `<game>-bg.jpg`: a dimmed, full-page fixed backdrop behind everything (a `body::before`, not `background-attachment: fixed`,
  which iOS Safari ignores).
* `<game>-hero.jpg`: the banner behind the title. An admin's `<GAME>_BANNER_URL` setting still overrides it.

All of it is the publisher's own promotional art, downloaded 2026-09-30 on the Captain's say-so, re-encoded (JPEG, at most 1920 px
wide) and used on a small private, friends-only, non-commercial site. None of it is formally licensed for reuse: the terms below are
what each publisher says, and anything here is removed on request. To change one, put a new file in `portal/static/games`, keep the
name, and update this table.

| File | Source (original) | Original size | Terms evidence |
|---|---|---|---|
| valheim-bg.jpg | https://img2.storyblok.com/1920x1080/f/157036/2560x1440/378da8af67/news-3.jpg (valheimgame.com media gallery, "news-3") | 1920x1080, 166 KB | No usage terms published. The EULA welcomes non-commercial creative use of game content and defers to a "Content Usage Guidelines" document that is not published. Grey. |
| valheim-hero.jpg | https://a.storyblok.com/f/157036/2132x1194/3754a641ab/carousel-bg.jpg (valheimgame.com hero background) | 2132x1194, 259 KB | Same as above. |
| factorio-bg.jpg | https://cdn.factorio.com/assets/img/artwork/rocket-man.png (factorio.com artwork page), top 11.5 % cropped off to remove the Factorio logo | 1920x1080, 4.5 MB | Wube's press page: "You are welcome to use any of the assets, screenshots, and artwork, that you can find on our website". The clearest of the five. |
| factorio-hero.jpg | https://cdn.cloudflare.steamstatic.com/steam/apps/427520/library_hero.jpg (Steam library hero, developer-supplied) | 1920x620, 868 KB | Developer store art; no reuse licence stated. |
| satisfactory-bg.jpg | https://cdn.cloudflare.steamstatic.com/steam/apps/526870/page_bg_raw.jpg (Steam store page background) | 1438x810, 2.2 MB | Developer store art; Coffee Stain publishes no fan-content terms. Grey. |
| satisfactory-hero.jpg | https://a.storyblok.com/f/110098/1920x803/5e73bc28d2/press-hero-bg.jpeg (satisfactorygame.com press page hero) | 1920x803, 299 KB | Same as above. |
| dragonwilds-bg.jpg | https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/1374490/f42c70c4dee9928dd4f9b5c0bf0cc3a615a425ac/page_bg_raw.jpg (Steam store page background) | 1438x810, 1.7 MB | Jagex's Fan Content Policy (v1.3) covers RuneScape: Dragonwilds fan sites; no wallpaper terms of its own. |
| dragonwilds-hero.jpg | https://cdn.cloudflare.steamstatic.com/steam/apps/1374490/library_hero_2x.jpg (Steam library hero 2x) | 3840x1240, 1.0 MB | Same as above. |
| everquest-bg.jpg | https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/205710/ss_dbcad62450756a25e68e42fee02aef726e4e8cdd.1920x1080.jpg (Steam store screenshot) | 1920x1080, 551 KB | Daybreak's Terms of Service allow non-commercial fan sites to share artwork and screenshots. |
| everquest-hero.jpg | https://cdn.cloudflare.steamstatic.com/steam/apps/205710/library_hero.jpg (Steam library hero) | 1920x620, 561 KB | Same as above. |

The page tests (`portal/tests/test_game_art.py`) fail if an image is missing, much larger than a page should carry, or not listed here.
