"""purple Skippy's bilby_favorite tool: marks the song the Captain is listening to on the portal's favorites list, or lists them."""
import house_portal_tools as T


def test_add_marks_what_is_playing_and_list_reads_it_back(monkeypatch):
    monkeypatch.setenv("SKIPPY_HOME_TOKEN", "tok")
    calls = []

    def request(method, url, body=None):
        calls.append((method, url, body))
        if method == "POST":
            return 200, {"favorite": {"title": "Porcelain", "artist": "Moby", "count": 2}}
        return 200, {"favorites": [{"title": "Porcelain", "artist": "Moby", "track": "Moby/Play/03 Porcelain.mp3"}]}
    assert T.bilby_favorite({"action": "add"}, request=request) == "Added to his favorites: Moby - Porcelain (already there; moved to the top)"
    assert calls[0][0] == "POST" and calls[0][1].endswith("/home/music/favorite") and calls[0][2] == {"source": "purple"}
    assert T.bilby_favorite({"action": "list"}, request=request) == "1. Moby - Porcelain"


def test_nothing_playing_is_said_plainly(monkeypatch):
    monkeypatch.setenv("SKIPPY_HOME_TOKEN", "tok")
    assert "Nothing is playing" in T.bilby_favorite({}, request=lambda m, u, b=None: (409, {}))
    assert "bilby_favorite" in T.TOOLS
