"""Standard Bloom filter (educational, stdlib only).

Deterministic hashing via BLAKE2b (never Python's randomized hash()).
A positive query means "possibly present", never proof of existence.
No deletion: bits persist for the session; reset() clears all state.
"""
import hashlib
import math


class BloomFilter:
    def __init__(self, size=512, num_hashes=3):
        if not isinstance(size, int) or size < 16:
            raise ValueError('Bloom size must be an int >= 16.')
        if not isinstance(num_hashes, int) or not 1 <= num_hashes <= 10:
            raise ValueError('num_hashes must be an int in 1..10.')
        self.size = size
        self.num_hashes = num_hashes
        self.reset()

    def reset(self):
        self.bits = [0] * self.size
        self.insertions = 0
        self.queries = 0
        self.positives = 0
        self._members = set()  # exact reference for demo/testing only, not part of the filter

    def positions(self, key):
        # Keyed hashing: BLAKE2b `key=` parameter with a per-function domain
        # separation string (the construction the report recommends). Each of
        # the k hash functions uses a distinct key, so positions are
        # deterministic yet independent across functions.
        data = str(key).encode('utf-8')
        out = []
        for i in range(self.num_hashes):
            h = hashlib.blake2b(data, digest_size=8, key=f'bloom-k{i}'.encode('utf-8'))
            out.append(int.from_bytes(h.digest(), 'big') % self.size)
        return out

    def add(self, key):
        for p in self.positions(key):
            self.bits[p] = 1
        self.insertions += 1
        self._members.add(str(key))

    def check(self, key, count_query=True):
        """Return 'possibly_present' or 'definitely_absent'. Query increments stats."""
        pos = self.positions(key)
        hit = all(self.bits[p] for p in pos)
        if count_query:
            self.queries += 1
            if hit:
                self.positives += 1
        return 'possibly_present' if hit else 'definitely_absent'

    def contains_bits(self, key):
        return all(self.bits[p] for p in self.positions(key))

    @property
    def bits_set(self):
        return sum(self.bits)

    @property
    def occupancy(self):
        return round(self.bits_set / self.size, 4) if self.size else 0.0

    def theoretical_fpp(self, n=None):
        """Standard formula: (1 - e^(-k*n/m))^k using insertion count."""
        n = self.insertions if n is None else n
        if n <= 0:
            return 0.0
        try:
            p = (1.0 - math.exp(-self.num_hashes * n / self.size)) ** self.num_hashes
        except OverflowError:
            p = 1.0
        return round(min(1.0, max(0.0, p)), 4)

    def exact_members(self):
        return set(self._members)

    def to_state(self, max_bits=512):
        bits = self.bits[:max_bits]
        return {
            'size': self.size,
            'num_hashes': self.num_hashes,
            'bits_set': self.bits_set,
            'occupancy': self.occupancy,
            'insertions': self.insertions,
            'queries': self.queries,
            'positives': self.positives,
            'theoretical_fpp': self.theoretical_fpp(),
            'bits': bits,
            'bits_truncated': len(self.bits) > max_bits,
        }
