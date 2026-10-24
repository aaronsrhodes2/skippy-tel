import time
import collections

class TTLCache:
    def __init__(self, capacity: int, ttl: float, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("Capacity must be at least 1")
        if ttl <= 0:
            raise ValueError("TTL must be positive")
        
        self.capacity = capacity
        self.ttl = ttl
        self.clock = clock
        self._cache = dict()
        self._order = collections.deque()
        self._expired = set()

    def _now(self):
        return self.clock()

    def _is_expired(self, key):
        stored_at = self._cache[key]["stored_at"]
        return self._now() - stored_at >= self.ttl

    def _remove_expired(self):
        now = self._now()
        expired_keys = [k for k in self._cache if self._is_expired(k)]
        for k in expired_keys:
            self._remove_key(k)

    def _remove_key(self, key):
        if key in self._cache:
            del self._cache[key]
            if key in self._expired:
                self._expired.remove(key)
            if key in self._order:
                self._order.remove(key)

    def put(self, key, value):
        now = self._now()
        # First, remove all expired entries
        self._remove_expired()
        
        # Check if key is already in cache
        if key in self._cache:
            # Update value and stored time
            self._cache[key]["value"] = value
            self._cache[key]["stored_at"] = now
            # Move to end of order (most recently used)
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)
            return

        # If capacity is full, evict least recently used (LRU)
        if len(self._cache) >= self.capacity:
            lru_key = self._order.popleft()
            self._remove_key(lru_key)
        
        # Add new entry
        self._cache[key] = {"value": value, "stored_at": now}
        self._order.append(key)

    def get(self, key, default=None):
        now = self._now()
        if key not in self._cache:
            return default

        if self._is_expired(key):
            self._remove_key(key)
            return default

        # Mark as most recently used
        if key in self._order:
            self._order.remove(key)
        self._order.append(key)
        return self._cache[key]["value"]

    def __len__(self):
        self._remove_expired()
        return len(self._cache)

    def __contains__(self, key):
        self._remove_expired()
        return key in self._cache

    def pop(self, key, default=None):
        now = self._now()
        if key not in self._cache:
            return default

        if self._is_expired(key):
            self._remove_key(key)
            return default

        value = self._cache[key]["value"]
        self._remove_key(key)
        return value
