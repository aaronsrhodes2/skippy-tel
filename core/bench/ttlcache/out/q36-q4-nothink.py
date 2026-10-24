import time
from collections import OrderedDict


class TTLCache:
    """In-memory cache with size limit and time-to-live expiry."""

    def __init__(self, capacity: int, ttl: float, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if ttl <= 0:
            raise ValueError("ttl must be > 0")
        self._capacity = capacity
        self._ttl = ttl
        self._clock = clock
        # OrderedDict to maintain insertion order (for LRU eviction)
        # Each entry: key -> (value, stored_at_time)
        self._cache: OrderedDict = OrderedDict()

    def put(self, key, value) -> None:
        """Store or replace the value and restart that key's expiry."""
        current_time = self._clock()

        # If key already exists, update value and move to end (most recently used)
        if key in self._cache:
            self._cache[key] = (value, current_time)
            self._cache.move_to_end(key)
            return

        # If cache is at capacity, first drop expired entries, then evict LRU if still full
        if len(self._cache) >= self._capacity:
            self._drop_expired()
            if len(self._cache) >= self._capacity:
                # Evict least recently used (first item in OrderedDict)
                self._cache.popitem(last=False)

        self._cache[key] = (value, current_time)

    def get(self, key, default=None):
        """Get value if present and not expired, else default."""
        current_time = self._clock()

        if key not in self._cache:
            return default

        value, stored_at = self._cache[key]

        # Check if expired
        if current_time - stored_at >= self._ttl:
            # Remove expired entry
            del self._cache[key]
            return default

        # Mark as most recently used
        self._cache.move_to_end(key)
        return value

    def __len__(self):
        """Number of entries that have not expired."""
        current_time = self._clock()
        # We need to clean up expired entries and count live ones
        expired_keys = []
        for key, (value, stored_at) in self._cache.items():
            if current_time - stored_at >= self._ttl:
                expired_keys.append(key)
            else:
                # Mark as recently used for live entries
                self._cache.move_to_end(key)
        for key in expired_keys:
            del self._cache[key]
        return len(self._cache)

    def __contains__(self, key):
        """True for a live entry, without changing recency."""
        if key not in self._cache:
            return False
        current_time = self._clock()
        _, stored_at = self._cache[key]
        return current_time - stored_at < self._ttl

    def pop(self, key, default=None):
        """Remove and return a live entry's value, else default."""
        if key not in self._cache:
            return default
        current_time = self._clock()
        value, stored_at = self._cache[key]
        if current_time - stored_at >= self._ttl:
            del self._cache[key]
            return default
        del self._cache[key]
        return value

    def _drop_expired(self):
        """Remove all expired entries."""
        current_time = self._clock()
        expired_keys = [
            key for key, (_, stored_at) in self._cache.items()
            if current_time - stored_at >= self._ttl
        ]
        for key in expired_keys:
            del self._cache[key]
