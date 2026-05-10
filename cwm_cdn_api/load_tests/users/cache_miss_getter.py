import uuid

from locust import task

from .base import BaseCdnUser
from .. import config


class CacheMissGetter(BaseCdnUser):
    if config.CWM_CDN_CACHE_MISS_GETTER_FIXED_COUNT > 0:
        fixed_count = config.CWM_CDN_CACHE_MISS_GETTER_FIXED_COUNT
    else:
        weight = config.CWM_CDN_CACHE_MISS_GETTER_WEIGHT

    @task
    def get_unique_path(self):
        self.cdn_request("GET", f"/anything/miss-{uuid.uuid4().hex}", name="cdn_get_cache_miss")
