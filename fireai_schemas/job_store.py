from __future__ import annotations
import os, json
from typing import Optional, Dict

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None


class JobStore:
    """
    Lightweight job store:
      - Uses Redis if REDIS_URL is set and redis package is available
      - Falls back to an in-memory dict otherwise
    Keys expire automatically in Redis via EX (TTL). Memory fallback does not.
    """
    def __init__(self, namespace: str = "fireai", ttl_seconds: int = 7 * 24 * 3600):
        self.namespace = namespace
        self.ttl = ttl_seconds
        url = os.getenv("REDIS_URL")
        self._r = None
        if url and redis:
            self._r = redis.from_url(url, decode_responses=True)
        self._mem: Dict[str, str] = {}

    def _key(self, job_id: str) -> str:
        return f"{self.namespace}:job:{job_id}"

    def set(self, job_id: str, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        if self._r is not None:
            self._r.set(self._key(job_id), data, ex=self.ttl)
        else:
            self._mem[self._key(job_id)] = data

    def get(self, job_id: str) -> Optional[dict]:
        k = self._key(job_id)
        if self._r is not None:
            raw = self._r.get(k)
            return json.loads(raw) if raw else None
        raw = self._mem.get(k)
        return json.loads(raw) if raw else None

    def exists(self, job_id: str) -> bool:
        k = self._key(job_id)
        if self._r is not None:
            return bool(self._r.exists(k))
        return k in self._mem
