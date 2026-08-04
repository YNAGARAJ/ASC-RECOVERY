"""Structural proof behind the authz matrix's tenant-isolation guarantee:
no route anywhere in this API accepts a client-supplied tenant identifier
-- not as a path param, query param, or request body field. `tenant_id`
is always resolved server-side from the bearer token (api.auth). Checked
two ways: by inspecting each route's actual parameter list (whitebox) and
by inspecting the generated OpenAPI schema (blackbox -- what a client
could see and try to manipulate).
"""

from __future__ import annotations

import inspect

from fastapi import FastAPI
from fastapi.routing import APIRoute

_FORBIDDEN_NAMES = {"tenant_id", "tenantid", "tenant"}


def test_no_route_signature_accepts_a_tenant_parameter(app: FastAPI) -> None:
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        signature = inspect.signature(route.endpoint)
        for name in signature.parameters:
            assert name.lower() not in _FORBIDDEN_NAMES, (route.path, name)


def test_openapi_schema_never_exposes_a_tenant_parameter(app: FastAPI) -> None:
    schema = app.openapi()
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            if not isinstance(operation, dict):
                continue
            for param in operation.get("parameters", []):
                assert param["name"].lower() not in _FORBIDDEN_NAMES, (path, method, param)
            request_body = operation.get("requestBody")
            if request_body is None:
                continue
            for content in request_body.get("content", {}).values():
                schema_ref = content.get("schema", {})
                properties = _resolve_properties(schema, schema_ref)
                for prop_name in properties:
                    assert prop_name.lower() not in _FORBIDDEN_NAMES, (path, method, prop_name)


def _resolve_properties(
    full_schema: dict[str, object], schema_ref: dict[str, object]
) -> dict[str, object]:
    ref = schema_ref.get("$ref")
    if isinstance(ref, str):
        # "#/components/schemas/SomeModel" -> full_schema["components"]["schemas"]["SomeModel"]
        _, _, path = ref.partition("#/")
        node: object = full_schema
        for part in path.split("/"):
            if not part:
                continue
            assert isinstance(node, dict)
            node = node[part]
        assert isinstance(node, dict)
        properties = node.get("properties", {})
        assert isinstance(properties, dict)
        return properties
    properties = schema_ref.get("properties", {})
    assert isinstance(properties, dict)
    return properties
