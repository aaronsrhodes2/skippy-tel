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

    def put(self, key, value):
        current_time = self.clock()
        # Remove all expired entries
        for k in list(self.cache.keys()):
            stored_at = self.cache[k][1]
            if current_time - stored_at >= self.ttl:
                del self.cache[k]
        # If key exists, remove it to update the expiry
        if key in self.cache:
            del self.cache[key]
        # Evict LRU entries if needed
        while len(self.cache) >= self.capacity:
            lru_key = next(iter(self.cache))
            del self.cache[lru_key]
        # Add new entry
        self.cache[key] = (value, current_time)

    def get(self, key, default=None):
        current_time = self.clock()
        if key in self.cache:
            stored_at = self.cache[key][1]
            if current_time - stored_at >= self.ttl:
                # Expired, remove and return default
                del self.cache[key]
                return default
            else:
                # Not expired, move to end (most recently used)
                self.cache.move_to_end(key, last=True)
                return self.cache[key][0]
        return default

    def __len__(self):
        current_time = self.clock()
        count = 0
        for key in self.cache:
            stored_at = self.cache[key][1]
            if current_time - stored_at < self.ttl:
                count += 1
        return count

    def __contains__(self, key):
        current_time = self.clock()
        if key in self.cache:
            stored_at = self.cache[key][1]
            return current_time - stored_at < self.ttl
        return False

    def pop(self, key, default=None):
        current_time = self.clock()
        if key in self.cache:
            stored_at = self.cache[key][1]
            if current_time - stored_at < self.ttl:
                value = self.cache[key][0]
                del self.cache[key]
                return value
        return default
