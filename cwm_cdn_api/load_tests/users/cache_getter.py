import random

from locust import task

from .base import BaseCdnUser
from .. import config


class CacheGetter(BaseCdnUser):
    if config.CWM_CDN_CACHE_GETTER_FIXED_COUNT > 0:
        fixed_count = config.CWM_CDN_CACHE_GETTER_FIXED_COUNT
    else:
        weight = config.CWM_CDN_CACHE_GETTER_WEIGHT

    def __init__(self, environment):
        super().__init__(environment)
        self.paths = config.CWM_CDN_CACHE_PATHS or [
            f"/anything/cache-{i}" for i in range(config.CWM_CDN_CACHE_KEY_COUNT)
        ]

    @task
    def get_cached_path(self):
        self.cdn_request("GET", random.choice(self.paths), name="cdn_get_cacheable")
