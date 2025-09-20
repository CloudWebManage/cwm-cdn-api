import logging

from fastapi import APIRouter, Body
from fastapi.responses import ORJSONResponse

from . import api


router = APIRouter()


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
                "cert": "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----",
                "key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
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
