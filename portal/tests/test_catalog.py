"""Pure-logic tests — no Flask/Authlib/google-cloud-compute needed, so
these run with nothing but the stdlib in any Python 3.10+."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog import GAMES, SERVICES, machine_for  # noqa: E402


def test_every_game_has_a_working_connect_string():
    for game in GAMES.values():
        text = game.connect("34.1.2.3", {})
        assert "34.1.2.3" in text or "Skippy" in text    # Dragonwilds: found by server name, no IP
        assert game.ram_gb > 0


def test_every_game_has_an_address_and_a_hint_for_where_to_type_it():
    for gid, g in GAMES.items():
        assert g.direct and g.direct("1.2.3.4", {}).startswith("1.2.3.4"), gid
        assert "In game" in g.direct_hint or gid == "everquest", gid           # (EverQuest's hint points at the file block)
        assert bool(g.invite_hint) == g.invite_code, gid                          # a game with a code says where to type it
    assert {g for g, v in GAMES.items() if v.invite_code} == {"dragonwilds", "valheim"}   # the only two that make one
    assert GAMES["dragonwilds"].via_list and not GAMES["valheim"].via_list


def test_a_valheim_password_may_not_appear_inside_the_world_name_or_seed():
    from catalog import missing_settings  # noqa: PLC0415
    ok = {"VALHEIM_PASSWORD": "otter123", "VALHEIM_SERVER_NAME": "Skippy Valheim"}
    assert missing_settings("valheim", ok) == []
    assert missing_settings("valheim", {**ok, "VALHEIM_PASSWORD": "money"}) == \
        ["Valheim's password must not appear inside the world seed."]              # inside the default seed, skippyhasyourmoney
    assert missing_settings("valheim", {**ok, "VALHEIM_PASSWORD": "money", "VALHEIM_WORLD_SEED": "keep"}) == []
    assert missing_settings("valheim", {**ok, "VALHEIM_PASSWORD": "Skippy"}) == \
        ["Valheim's password must not appear inside the world name.", "Valheim's password must not appear inside the world seed."]


def test_the_default_seed_is_filled_in_like_the_other_defaults():
    import autoconfig  # noqa: PLC0415
    plan = autoconfig.plan({}, {"valheim": GAMES["valheim"]})
    assert plan.set["VALHEIM_WORLD_SEED"] == "skippyhasyourmoney"
    assert "VALHEIM_WORLD_SEED" not in autoconfig.plan({"VALHEIM_WORLD_SEED": "keep"}, {"valheim": GAMES["valheim"]}).set


def test_dragonwilds_connect_uses_configured_world_name():
    text = GAMES["dragonwilds"].connect("34.1.2.3", {"DRAGONWILDS_WORLD_NAME": "Skippy"})
    assert "Skippy" in text
    assert "Ashenfall" not in text


def test_machine_for_sizes_match_games_sh():
    assert machine_for(["factorio"]) == "e2-standard-2"
    assert machine_for(["valheim"]) == "e2-standard-2"
    assert machine_for(["valheim", "factorio"]) == "e2-standard-4"
    assert machine_for(["satisfactory", "dragonwilds"]) == "e2-highmem-4"
    assert machine_for(["valheim", "factorio", "satisfactory", "dragonwilds"]) == "e2-highmem-4"


def test_machine_for_ignores_unknown_game_ids():
    assert machine_for(["not-a-real-game"]) == "e2-standard-2"


def test_services_have_no_duplicate_ids():
    ids = [s.id for s in SERVICES]
    assert len(ids) == len(set(ids))


def test_valheims_defaults_agree_everywhere_they_are_written_down():
    """The VM (compose, valheim_seed.py), the portal's settings and Discord's copy must mean the same by 'blank'."""
    import autoconfig  # noqa: PLC0415
    from catalog import VALHEIM_DEFAULTS, valheim_crossplay  # noqa: PLC0415
    root = Path(__file__).resolve().parents[2]
    compose = (root / "games" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "CROSSPLAY: ${VALHEIM_CROSSPLAY:-%s}" % VALHEIM_DEFAULTS["VALHEIM_CROSSPLAY"] in compose
    assert all(autoconfig.DEFAULTS[k] == v for k, v in VALHEIM_DEFAULTS.items())
    notify = (root / "infra" / "gcp-games" / "discord-notify.py").read_text(encoding="utf-8")
    assert 'e.get("VALHEIM_CROSSPLAY") or "%s"' % VALHEIM_DEFAULTS["VALHEIM_CROSSPLAY"] in notify
    assert not valheim_crossplay({}) and valheim_crossplay({"VALHEIM_CROSSPLAY": " True "}) and not valheim_crossplay({"VALHEIM_CROSSPLAY": "false"})


def test_the_pre_flight_join_password_check_applies_the_world_and_seed_rule_too():
    import autoconfig  # noqa: PLC0415
    game = {"valheim": GAMES["valheim"]}
    with pytest.raises(autoconfig.PlanError, match="world's name or seed"):
        autoconfig.plan({"VALHEIM_PASSWORD": "otter123"}, game, join_password="money")       # inside the default seed
    assert autoconfig.plan({"VALHEIM_PASSWORD": "otter123"}, game, join_password="otter456").set["VALHEIM_PASSWORD"] == "otter456"
    plan = autoconfig.plan({"VALHEIM_WORLD_SEED": "keep"}, game, join_password="money")      # `keep`: the seed isn't ours to check
    assert plan.set["VALHEIM_PASSWORD"] == "money"


def test_nothing_still_points_at_the_join_password_reveal_that_was_removed():
    root = Path(__file__).resolve().parents[2]
    offenders = [str(p.relative_to(root)) for pattern in ("portal/*.py", "portal/templates/*.html", "docs/**/*.md", "*.md")
                 for p in root.glob(pattern) if "Show join password" in p.read_text(encoding="utf-8", errors="ignore")]
    assert offenders == []


def test_satisfactory_tells_people_to_use_the_server_manager_not_join_game():
    """Reported 2026-09-30: Multiplayer -> Join Game (the 'IP or Session ID' screen, for player-hosted games) fails on a
    dedicated server with 'Encryption Token Missing'; the Server Manager handshake is the only way in."""
    game = GAMES["satisfactory"]
    for text in (game.direct_hint, game.connect("1.2.3.4", {})):
        assert "Server Manager" in text and "Join Game" in text and "Encryption Token Missing" in text
    assert "first player to connect claims" in game.direct_hint
    notify = (Path(__file__).resolve().parents[2] / "infra" / "gcp-games" / "discord-notify.py").read_text(encoding="utf-8")
    assert "Encryption Token Missing" in notify


def test_everquests_eqhost_block_is_the_rof2_format_not_titanium():
    """Reported 2026-09-30: the page gave the Titanium-era [Registration Servers]/[Login Servers] braces block; the RoF2
    client can't read it, replaces the file with its stock one (Host=xp-smitchell:7308, a dead developer host) and login
    shows 'a timeout has occurred'. RoF2 wants [LoginServer] / Host=ip:port."""
    game = GAMES["everquest"]
    label, block = game.snippet("1.2.3.4", {})
    assert block.splitlines() == ["[LoginServer]", "Host=1.2.3.4:5999", "Host1="]
    assert "Registration Servers" not in block and "{" not in block
    for text in (game.connect("1.2.3.4", {}), game.direct_hint):
        assert "xp-smitchell" in text or "[LoginServer]" in text
    assert "RoF2" in label and "read-only" in game.direct_hint
    notify = (Path(__file__).resolve().parents[2] / "infra" / "gcp-games" / "discord-notify.py").read_text(encoding="utf-8")
    assert "[LoginServer]" in notify and "Registration Servers" not in notify
