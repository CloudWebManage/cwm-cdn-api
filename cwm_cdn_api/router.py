import logging

from fastapi import APIRouter, Body
from fastapi.responses import ORJSONResponse

from . import api
from . import purge as purge_api
from . import config


router = APIRouter()


def _public_pop_result(pop, selector_count):
    result = {
        'pop': pop.get('popId') or pop.get('pop') or 'unknown',
        'popId': pop.get('popId') or pop.get('pop') or 'unknown',
        'status': pop.get('status', 'failed'),
        'purgedSelectors': pop.get('purgedSelectors', selector_count if pop.get('success') else 0),
        'message': pop.get('message') or '',
    }
    for key in ('success', 'durationSeconds', 'latencySeconds', 'statusCode', 'shards'):
        if key in pop:
            result[key] = pop[key]
    return result


@router.get("/", include_in_schema=False)
async def root():
    logging.debug('Root endpoint called')
    return {"ok": True}


@router.post("/apply")
async def apply(
    cdn_tenant_name: str,
    cdn_tenant_spec: dict = Body(..., example={
        "domains": [
            {
                "name": "test.example.com",
                "tls": {
                    "mode": "provided",
                    "minVersion": "TLSv1.2",
                    "maxVersion": "TLSv1.3",
                    "redirectHttpToHttps": False,
                },
                "cert": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----",
                "key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
            },
            {
                "name": "customer-owned.example.com",
                "tls": {
                    "mode": "letsencrypt",
                    "redirectHttpToHttps": True,
                }
            }
        ],
        "origins": [
            {
                "url": "http://example.com",
            }
        ]
    })
):
    success, output = await api.apply(cdn_tenant_name, cdn_tenant_spec)
    return ORJSONResponse(
        status_code=200 if success else 400,
        content={
            "success": success,
            "msg": output
        }
    )


@router.get("/debug/certificates")
async def debug_certificates(cdn_tenant_name: str, primary_key: str):
    success, output = await api.debug_certificates(cdn_tenant_name, primary_key)
    return ORJSONResponse(
        status_code=200 if success else 403,
        content={
            "success": success,
            "debug": output if success else None,
            "msg": None if success else output,
        }
    )


@router.post("/delete")
async def delete(cdn_tenant_name: str, primary_key: str = ""):
    success, output = await api.delete(cdn_tenant_name, primary_key)
    return ORJSONResponse(
        status_code=200 if success else 400,
        content={
            "success": success,
            "msg": output
        }
    )


@router.get("/get")
async def get(cdn_tenant_name: str):
    success, output = await api.get(cdn_tenant_name)
    return ORJSONResponse(
        status_code=200 if success else 400,
        content={
            "success": success,
            "tenant": output if success else None,
            "msg": None if success else output
        }
    )


@router.get("/list")
async def list_tenants():
    return [name async for name in api.list_iterator()]


@router.get('/reserved-names')
async def reserved_names():
    return [name async for name in api.reserved_names_iterator()]


@router.get('/components-status')
async def components_status():
    return await api.components_status()


async def _purge_response(cdn_tenant_name, body, primary_key, everything=False):
    propagated_from_primary = not config.IS_PRIMARY
    if propagated_from_primary and (not config.ALLOWED_PRIMARY_KEY or not primary_key or primary_key != config.ALLOWED_PRIMARY_KEY):
        return ORJSONResponse(status_code=403, content={
            'success': False,
            'msg': 'Purge is not allowed on this instance',
        })
    found, tenant_or_msg = await api.get_tenant_object(cdn_tenant_name)
    if not found:
        return ORJSONResponse(status_code=404, content={
            'success': False,
            'msg': tenant_or_msg,
        })
    try:
        if propagated_from_primary and body and body.get('operationId'):
            operation_id = body['operationId']
            if everything:
                for key in (body or {}).keys():
                    if key not in {'operationId', 'scope'}:
                        raise ValueError(f'Unsupported propagated purge-everything request field: {key}')
                selectors = []
            else:
                for key in (body or {}).keys():
                    if key not in {'operationId', 'selectors'}:
                        raise ValueError(f'Unsupported propagated purge request field: {key}')
                selectors = purge_api.validate_normalized_selectors(body.get('selectors'))
        else:
            operation_id = purge_api.new_operation_id()
            selectors = [] if everything else purge_api.normalize_purge_selectors(body or {}, tenant_or_msg)
    except ValueError as exc:
        return ORJSONResponse(status_code=400, content={
            'success': False,
            'msg': str(exc),
        })
    local = await purge_api.purge_local(operation_id, cdn_tenant_name, selectors=selectors, everything=everything)
    secondaries = await purge_api.propagate_to_secondaries(
        operation_id,
        '/purge-everything' if everything else '/purge',
        cdn_tenant_name,
        purge_api.normalized_purge_body(operation_id, selectors=selectors, everything=everything),
    )
    pops = [local, *secondaries]
    success = all(pop.get('success') for pop in pops)
    status = 'success' if success else ('partial' if any(pop.get('success') for pop in pops) else 'failed')
    selector_count = 0 if everything else len(selectors)
    results = [_public_pop_result(pop, selector_count) for pop in pops]
    return ORJSONResponse(status_code=200 if success else 207, content={
        'success': success,
        'accepted': True,
        'status': status,
        'operationId': operation_id,
        'tenant': cdn_tenant_name,
        'scope': 'everything' if everything else 'selectors',
        'selectors': [] if everything else purge_api.public_selectors(selectors),
        'results': results,
        'pops': pops,
    })


@router.post('/purge')
async def purge(cdn_tenant_name: str, primary_key: str = '', body: dict = Body(default_factory=dict)):
    return await _purge_response(cdn_tenant_name, body, primary_key, everything=False)


@router.post('/purge-everything')
async def purge_everything(cdn_tenant_name: str, primary_key: str = '', body: dict = Body(default_factory=dict)):
    allowed_fields = {'confirm'} if config.IS_PRIMARY else {'confirm', 'operationId', 'scope'}
    for key in (body or {}).keys():
        if key not in allowed_fields:
            return ORJSONResponse(status_code=400, content={
                'success': False,
                'msg': f'Unsupported purge-everything request field: {key}',
            })
    return await _purge_response(cdn_tenant_name, body or {}, primary_key, everything=True)


@router.get('/origins-health')
async def origins_health(cdn_tenant_name: str):
    success, origins = await api.origins_health(cdn_tenant_name)
    return ORJSONResponse(
        status_code=200 if success else 400,
        content={
            "success": success,
            "tenant": cdn_tenant_name,
            "origins": origins if success else [],
            "msg": None if success else origins,
        }
    )
