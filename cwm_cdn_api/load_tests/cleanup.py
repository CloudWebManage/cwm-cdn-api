from . import config
from .state import _request_api, get_state


def _tenant_names_for_cleanup():
    configured = {tenant["name"] for tenant in get_state().build_tenants()}
    res = _request_api("GET", "list")
    res.raise_for_status()
    existing = res.json()
    if config.CWM_CDN_TENANTS or config.CWM_CDN_TENANT_NAMES:
        return [name for name in existing if name in configured]
    prefix = f"{config.CWM_CDN_TENANT_PREFIX}-"
    return [name for name in existing if name.startswith(prefix)]


def main():
    for tenant_name in _tenant_names_for_cleanup():
        print(f"Deleting CDN load-test tenant: {tenant_name}")
        res = _request_api("POST", "delete", params={"cdn_tenant_name": tenant_name})
        if res.status_code != 200:
            print(f"Failed to delete {tenant_name}: {res.status_code} {res.text}")
