import json
import logging
import random
import time

import urllib3
from locust import User, between
from urllib3 import HTTPSConnectionPool, Timeout

from .. import config
from ..state import get_state


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class BaseCdnUser(User):
    abstract = True
    wait_time = between(config.CWM_CDN_WAIT_TIME_MIN_SECONDS, config.CWM_CDN_WAIT_TIME_MAX_SECONDS)

    def __init__(self, environment):
        super().__init__(environment)
        self.state = get_state()
        self.pools = {}

    def on_start(self):
        self.state.initialize(create_tenants=False)

    def get_pool(self, edge_target, tenant):
        """returns a reusable https connection pool for given tenant and edge_target"""
        key = (edge_target["address"], tenant["domain"])
        if key not in self.pools:
            cert_reqs = "CERT_REQUIRED" if config.CWM_CDN_EDGE_VERIFY_TLS else "CERT_NONE"
            self.pools[key] = HTTPSConnectionPool(
                edge_target["address"],
                port=443,
                cert_reqs=cert_reqs,
                assert_hostname=tenant["domain"] if config.CWM_CDN_EDGE_VERIFY_TLS else False,
                server_hostname=tenant["domain"],
                timeout=Timeout(
                    connect=config.CWM_CDN_EDGE_CONNECT_TIMEOUT_SECONDS,
                    read=config.CWM_CDN_EDGE_READ_TIMEOUT_SECONDS,
                ),
                maxsize=1,
            )
        return self.pools[key]

    def choose_tenant(self):
        return random.choice(self.state.tenants)

    def choose_edge_target(self):
        return random.choice(self.state.edge_targets)

    def cdn_request(self, method, path, *, name, tenant=None, edge_target=None, headers=None, body=None, expected_statuses=(200,)):
        tenant = tenant or self.choose_tenant()
        edge_target = edge_target or self.choose_edge_target()
        headers = {
            "Host": tenant["domain"],
            "User-Agent": "cwm-cdn-load-test/1.0",
            **(headers or {}),
        }
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        if isinstance(body, str):
            body = body.encode("utf-8")
        if not path.startswith("/"):
            path = f"/{path}"

        start_time = time.perf_counter()
        response = None
        response_length = 0
        exception = None
        try:
            self.state.debug("Starting CDN request to %s via %s: %s %s", tenant["domain"], edge_target["address"], method, path)
            response = self.get_pool(edge_target, tenant).urlopen(
                method,
                path,
                headers=headers,
                body=body,
                preload_content=True,
                retries=False,
                redirect=False,
            )
            response_length = len(response.data or b"")
            if response.status not in expected_statuses:
                exception = RuntimeError(
                    f"Unexpected status {response.status} from {tenant['domain']} via {edge_target['address']}"
                )
            self.state.debug("Completed CDN request to %s via %s (response_lengeh=%s, exception=%s)", tenant["domain"], edge_target["address"], response_length, exception)
            return response
        except Exception as exc:
            exception = exc
            self.state.debug("Exception in CDN request to %s via %s: %s", tenant["domain"], edge_target["address"], exception)
            logging.debug("CDN request failed", exc_info=True)
            return None
        finally:
            self.environment.events.request.fire(
                request_type=method,
                name=name,
                response_time=(time.perf_counter() - start_time) * 1000,
                response_length=response_length,
                response=response,
                context={"tenant": tenant["name"], "edge": edge_target["address"]},
                exception=exception,
            )
