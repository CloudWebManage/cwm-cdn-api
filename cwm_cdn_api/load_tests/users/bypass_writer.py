import uuid

from locust import task

from .base import BaseCdnUser
from .. import config


class BypassWriter(BaseCdnUser):
    if config.CWM_CDN_BYPASS_WRITER_FIXED_COUNT > 0:
        fixed_count = config.CWM_CDN_BYPASS_WRITER_FIXED_COUNT
    else:
        weight = config.CWM_CDN_BYPASS_WRITER_WEIGHT

    @task
    def post_bypass(self):
        self.cdn_request(
            "POST",
            f"/anything/bypass-{uuid.uuid4().hex}",
            name="cdn_post_bypass",
            body={"source": "cwm-cdn-load-test"},
        )
