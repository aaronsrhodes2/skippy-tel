import time
from collections import OrderedDict


class TTLCache:
    """An in-memory cache with a size limit and time-to-live expiry."""

    def __init__(self, capacity: int, ttl: float, clock=time.monotonic):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if ttl <= 0:
            raise ValueError("ttl must be > 0")
        
        self._capacity = capacity
        self._ttl = ttl
        self._clock = clock
        # OrderedDict to maintain insertion order (for LRU eviction)
        # We store entries as: key -> (value, stored_at)
        self._cache: OrderedDict = OrderedDict()

    def put(self, key, value):
        """Store or replace the value and restart that key's expiry."""
        current_time = self._clock()
        
        # If key already exists, update it and move to end (most recently used)
        if key in self._cache:
            self._cache[key] = (value, current_time)
            self._cache.move_to_end(key)
            return
        
        # If key doesn't exist, we need to evict if necessary
        # First, drop every expired entry
        self._remove_expired(current_time)
        
        # Check if we still exceed capacity
        if len(self._cache) >= self._capacity:
            # Evict the least recently used one (first item in OrderedDict)
            self._cache.popitem(last=False)
        
        # Add the new entry
        self._cache[key] = (value, current_time)

    def get(self, key, default=None):
        """Get the value if present and not expired, else default."""
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
        """Return the number of entries that have not expired."""
        current_time = self._clock()
        # Remove expired entries and count live ones
        # We need to iterate and remove expired ones
        expired_keys = [
            key for key, (value, stored_at) in self._cache.items()
            if current_time - stored_at >= self._ttl
        ]
        for key in expired_keys:
            del self._cache[key]
        return len(self._cache)

    def __contains__(self, key):
        """Return True for a live entry, without changing recency."""
        current_time = self._clock()
        
        if key not in self._cache:
            return False
        
        value, stored_at = self._cache[key]
        
        # Check if expired
        if current_time - stored_at >= self._ttl:
            # Remove expired entry
            del self._cache[key]
            return False
        
        return True

    def pop(self, key, default=None):
        """Remove and return a live entry's value, else default."""
        current_time = self._clock()
        
        if key not in self._cache:
            return default
        
        value, stored_at = self._cache[key]
        
        # Check if expired
        if current_time - stored_at >= self._ttl:
            del self._cache[key]
            return default
        
        # Remove and return the value
        del self._cache[key]
        return value

    def _remove_expired(self, current_time):
        """Remove all expired entries from the cache."""
        expired_keys = [
            key for key, (value, stored_at) in self._cache.items()
            if current_time - stored_at >= self._ttl
        ]
        for key in expired_keys:
            del self._cache[key]
