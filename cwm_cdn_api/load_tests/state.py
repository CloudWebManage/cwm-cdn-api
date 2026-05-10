import copy
import ipaddress
import json
import logging
import socket
import time

import requests

from . import config


def _api_auth():
    if config.CWM_CDN_API_USERNAME or config.CWM_CDN_API_PASSWORD:
        return config.CWM_CDN_API_USERNAME, config.CWM_CDN_API_PASSWORD
    return None


def _request_api(method, path, **kwargs):
    if not config.CWM_CDN_API_URL:
        raise RuntimeError("CWM_CDN_API_URL is required")
    return requests.request(
        method,
        f"{config.CWM_CDN_API_URL}/{path.lstrip('/')}",
        timeout=30,
        auth=_api_auth(),
        verify=config.CWM_CDN_API_VERIFY_TLS,
        **kwargs,
    )


def _is_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _resolve_host(host):
    addresses = set()
    for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
        addresses.add(result[4][0])
    return sorted(addresses)


class LoadTestState:
    def __init__(self):
        self.tenants = []
        self.edge_targets = []
        self.initialized = False

    def debug(self, *args, **kwargs):
        if config.CWM_CDN_LOAD_TESTS_DEBUG:
            logging.info(*args, **kwargs)

    def build_tenants(self):
        if config.CWM_CDN_TENANTS:
            assert not config.CWM_CDN_TENANTS and not config.CWM_CDN_NUM_TENANTS
            tenants = []
            for value in config.CWM_CDN_TENANTS:
                if ":" in value:
                    name, domain = value.split(":", 1)
                else:
                    name = value
                    assert config.CWM_CDN_TENANT_DOMAIN_SUFFIX
                    domain = f"{value}.{config.CWM_CDN_TENANT_DOMAIN_SUFFIX}"
                tenants.append({"name": name, "domain": domain})
            return tenants
        if config.CWM_CDN_TENANT_NAMES:
            assert not config.CWM_CDN_NUM_TENANTS
            assert config.CWM_CDN_TENANT_DOMAIN_SUFFIX
            return [
                {"name": name, "domain": f"{name}.{config.CWM_CDN_TENANT_DOMAIN_SUFFIX}"}
                for name in config.CWM_CDN_TENANT_NAMES
            ]
        assert config.CWM_CDN_NUM_TENANTS and config.CWM_CDN_TENANT_DOMAIN_SUFFIX
        return [
            {
                "name": f"{config.CWM_CDN_TENANT_PREFIX}-{i}",
                "domain": f"{config.CWM_CDN_TENANT_PREFIX}-{i}.{config.CWM_CDN_TENANT_DOMAIN_SUFFIX}",
            }
            for i in range(config.CWM_CDN_NUM_TENANTS)
        ]

    def resolve_edge_targets(self):
        targets = [{"name": ip, "address": ip} for ip in config.CWM_CDN_EDGE_IPS]
        hosts = list(config.CWM_CDN_EDGE_HOSTS)
        for host in hosts:
            if _is_ip(host):
                self.debug("Found host %s", host)
                targets.append({"name": host, "address": host})
                continue
            for address in _resolve_host(host):
                self.debug("Found host %s - %s", host, address)
                targets.append({"name": host, "address": address})
        seen = set()
        deduped = []
        for target in targets:
            key = (target["name"], target["address"])
            if key not in seen:
                seen.add(key)
                deduped.append(target)
        if not deduped:
            raise RuntimeError("No CDN edge targets configured.")
        return deduped

    def load_tenant_spec(self, tenant):
        with open(config.CWM_CDN_TENANT_SPEC_PATH, "r") as f:
            spec = json.load(f)
        spec = copy.deepcopy(spec)
        spec["domains"][0]["name"] = tenant["domain"]
        spec["origins"][0]["url"] = config.CWM_CDN_ORIGIN_URL
        if config.CWM_CDN_ES_ENDPOINTS:
            es_config = {
                "ENDPOINTS": json.dumps(config.CWM_CDN_ES_ENDPOINTS),
            }
            if config.CWM_CDN_ES_AUTH:
                es_config["AUTH"] = config.CWM_CDN_ES_AUTH
            if config.CWM_CDN_ES_BULK:
                es_config["BULK"] = config.CWM_CDN_ES_BULK
            spec["elasticsearch"] = {
                "enabled": True,
                "config": es_config,
            }
        return spec

    def create_tenant(self, tenant):
        self.debug("Applying CDN tenant %s", tenant["name"])
        res = _request_api(
            "POST",
            "apply",
            params={"cdn_tenant_name": tenant["name"]},
            json=self.load_tenant_spec(tenant),
        )
        if res.status_code != 200:
            raise RuntimeError(f"Failed to apply tenant {tenant['name']}: {res.status_code} {res.text}")
        data = res.json()
        if not data.get("success"):
            raise RuntimeError(f"Failed to apply tenant {tenant['name']}: {data.get('msg')}")

    def wait_tenant_ready(self, tenant):
        deadline = time.monotonic() + config.CWM_CDN_TENANT_READY_TIMEOUT_SECONDS
        last_error = None
        while time.monotonic() < deadline:
            try:
                res = _request_api("GET", "get", params={"cdn_tenant_name": tenant["name"]})
                if res.status_code == 200:
                    data = res.json()
                    self.debug("Tenant: %s", data)
                    if data.get("tenant", {}).get("ready"):
                        self.debug("CDN tenant %s is ready", tenant["name"])
                        return
                    last_error = data
                else:
                    last_error = f"{res.status_code} {res.text}"
            except Exception as exc:
                last_error = str(exc)
            time.sleep(config.CWM_CDN_TENANT_READY_POLL_SECONDS)
        raise RuntimeError(f"Timed out waiting for CDN tenant {tenant['name']} to become ready: {last_error}")

    def delete_tenant(self, tenant):
        self.debug("Deleting CDN tenant %s", tenant["name"])
        res = _request_api("POST", "delete", params={"cdn_tenant_name": tenant["name"]})
        if res.status_code not in (200, 400):
            raise RuntimeError(f"Failed to delete tenant {tenant['name']}: {res.status_code} {res.text}")
        if res.status_code == 400 and "not found" not in res.text.lower():
            raise RuntimeError(f"Failed to delete tenant {tenant['name']}: {res.status_code} {res.text}")

    def initialize(self, create_tenants=False, wait_for_ready=True):
        if not self.initialized:
            self.tenants = self.build_tenants()
            self.edge_targets = self.resolve_edge_targets()
            self.initialized = True
            logging.info(
                "CDN load test initialized with %s tenants and %s edge targets",
                len(self.tenants),
                len(self.edge_targets),
            )
        if create_tenants:
            for tenant in self.tenants:
                self.create_tenant(tenant)
        if wait_for_ready:
            for tenant in self.tenants:
                self.wait_tenant_ready(tenant)

    def teardown(self):
        if config.CWM_CDN_KEEP_TENANTS:
            logging.warning("CWM_CDN_KEEP_TENANTS is true, will not delete tenants")
            return
        logging.info("Deleting CDN tenants...")
        errors = []
        for tenant in self.tenants or self.build_tenants():
            try:
                self.delete_tenant(tenant)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("Errors deleting CDN tenants:\n" + "\n".join(errors))


_state = LoadTestState()


def get_state():
    return _state
