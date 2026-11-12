"""Bilby favorites (favorites.py): the Captain marks what he is listening to, from the player, purple Skippy or a pod, for DJing later."""
import favorites as F


def devices():
    return [{"id": "laptop", "name": "Laptop", "state": {"track": "Daft Punk/Discovery/01 One More Time.mp3", "title": "One More Time",
                                                       "artist": "Daft Punk", "playing": False, "pos": 10}},
            {"id": "skippyroom-office", "name": "Office pod", "state": {"track": "Moby/Play/03 Porcelain.mp3", "title": "Porcelain",
                                                                       "artist": "Moby", "playing": True, "pos": 42}}]


def test_the_song_playing_now_is_the_one_marked_and_the_asking_pod_wins():
    assert F.now_playing(devices())["title"] == "Porcelain"
    assert F.now_playing(devices(), prefer="laptop")["title"] == "One More Time"
    assert F.now_playing([]) is None


def test_marking_again_moves_it_to_the_top_and_counts_it_and_the_list_exports_for_traktor():
    t = {"now": 100}
    fav = F.Favorites(clock=lambda: t["now"])
    fav.add(devices()[1]["state"], source="pod")
    fav.add(devices()[0]["state"], source="purple")
    t["now"] = 200
    again = fav.add(devices()[1]["state"], source="player")
    assert again["count"] == 2 and [f["title"] for f in fav.items()] == ["Porcelain", "One More Time"]
    m3u = fav.m3u()
    assert m3u.startswith("#EXTM3U") and "Moby/Play/03 Porcelain.mp3" in m3u and "#EXTINF:-1,Moby - Porcelain" in m3u
    assert fav.remove("Moby/Play/03 Porcelain.mp3") and [f["title"] for f in fav.items()] == ["One More Time"]


def test_a_spotify_favorite_is_kept_by_its_uri_and_is_a_comment_line_in_the_m3u():
    fav = F.Favorites(clock=lambda: 100)
    uri = "spotify:track:4uLU6hMCjMI75M1A2tKUQC"
    fav.add({"track": uri, "title": "Never Gonna Give You Up", "artist": "Rick Astley", "playing": True}, source="voice", where="House stereo")
    fav.add(devices()[1]["state"], source="pod")
    lines = fav.m3u().splitlines()
    assert f"# Spotify: Rick Astley - Never Gonna Give You Up {uri}" in lines
    assert uri not in [ln for ln in lines if not ln.startswith("#")]          # Traktor only sees files
    assert "Moby/Play/03 Porcelain.mp3" in lines and fav.items()[1]["track"] == uri
