"""Grades a ttlcache answer: python check.py answer.py -> prints passed/total, exits 0 only when all pass."""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("answer", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def cache(cap=3, ttl=10):
    c = Clock()
    return mod.TTLCache(cap, ttl, clock=c), c


def t_basic():
    s, _ = cache(); s.put("a", 1); assert s.get("a") == 1 and s.get("b", 7) == 7 and len(s) == 1
def t_replace():
    s, _ = cache(); s.put("a", 1); s.put("a", 2); assert s.get("a") == 2 and len(s) == 1
def t_lru():
    s, _ = cache(); s.put("a", 1); s.put("b", 2); s.put("c", 3); s.get("a"); s.put("d", 4)
    assert "b" not in s and s.get("a") == 1 and s.get("c") == 3 and s.get("d") == 4
def t_contains_no_recency():
    s, _ = cache(); s.put("a", 1); s.put("b", 2); s.put("c", 3); assert "a" in s; s.put("d", 4); assert "a" not in s
def t_expiry_boundary():
    s, c = cache(); s.put("a", 1); c.t = 9.999; assert s.get("a") == 1; c.t = 10; assert s.get("a") is None
def t_put_restarts():
    s, c = cache(); s.put("a", 1); c.t = 8; s.put("a", 2); c.t = 15; assert s.get("a") == 2
def t_get_does_not_restart():
    s, c = cache(); s.put("a", 1); c.t = 8; s.get("a"); c.t = 10; assert s.get("a") is None
def t_len_counts_live():
    s, c = cache(); s.put("a", 1); c.t = 5; s.put("b", 2); c.t = 11; assert len(s) == 1
def t_expired_swept_before_lru():
    s, c = cache(); s.put("a", 1); c.t = 1; s.put("b", 2); s.put("c", 3); s.get("a")
    c.t = 10.5; s.put("d", 4)   # a expired: it goes, b and c stay
    assert s.get("b") == 2 and s.get("c") == 3 and s.get("d") == 4
def t_pop():
    s, c = cache(); s.put("a", 1); assert s.pop("a") == 1 and s.pop("a", 9) == 9 and len(s) == 0
    s.put("b", 2); c.t = 20; assert s.pop("b", 5) == 5
def t_bad_args():
    for cap, ttl in ((0, 1), (1, 0), (-1, 5), (2, -1)):
        try:
            mod.TTLCache(cap, ttl); return False
        except ValueError:
            pass
def t_falsy_values():
    s, _ = cache(); s.put("z", 0); s.put("n", None); assert s.get("z", 5) == 0 and "n" in s and s.get("n", 5) is None
def t_capacity_one():
    s, _ = cache(1); s.put("a", 1); s.put("b", 2); assert "a" not in s and s.get("b") == 2
def t_default_clock():
    s = mod.TTLCache(2, 100); s.put("a", 1); assert s.get("a") == 1


ok = 0
tests = [v for k, v in list(globals().items()) if k.startswith("t_")]
for t in tests:
    try:
        if t() is False:
            raise AssertionError("no ValueError")
        ok += 1
    except Exception as x:
        print(f"FAIL {t.__name__}: {x!r}")
print(f"{ok}/{len(tests)}")
sys.exit(0 if ok == len(tests) else 1)
