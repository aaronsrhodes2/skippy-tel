import time
from collections import OrderedDict

class TTLCache:
    def __init__(self, capacity: int, ttl: float, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if ttl <= 0:
            raise ValueError("ttl must be > 0")
        self.capacity = capacity
        self.ttl = ttl
        self.clock = clock
        self.cache = OrderedDict()

    def _sweep_expired(self):
        now = self.clock()
        expired_keys = [k for k, (_, exp) in self.cache.items() if now - exp >= self.ttl]
        for k in expired_keys:
            del self.cache[k]

    def put(self, key, value):
        if key in self.cache:
            self.cache[key] = (value, self.clock() + self.ttl)
            self.cache.move_to_end(key)
            return
        if len(self.cache) >= self.capacity:
            self._sweep_expired()
        if len(self.cache) >= self.capacity:
            self.cache.popitem(last=False)
        self.cache[key] = (value, self.clock() + self.ttl)

    def get(self, key, default=None):
        if key not in self.cache:
            return default
        value, expires_at = self.cache[key]
        now = self.clock()
        if now - expires_at >= self.ttl:
            del self.cache[key]
            return default
        self.cache.move_to_end(key)
        return value

    def __len__(self):
        self._sweep_expired()
        return len(self.cache)

    def __contains__(self, key):
        if key not in self.cache:
            return False
        _, expires_at = self.cache[key]
        now = self.clock()
        if now - expires_at >= self.ttl:
            del self.cache[key]
            return False
        return True

    def pop(self, key, default=None):
        if key not in self.cache:
            return default
        value, expires_at = self.cache[key]
        now = self.clock()
        if now - expires_at >= self.ttl:
            del self.cache[key]
            return default
        del self.cache[key]
        return value
