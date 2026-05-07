import json
import os
import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

app = FastAPI(title="FastCart API Gateway", docs_url=None, openapi_url=None)

GATEWAY_CONFIG = {
    "endpoints": {
        "/auth":     {"backend": "http://fastcart-auth:8001"},
        "/products": {"backend": "http://fastcart-product:8002"},
        "/orders":   {"backend": "http://fastcart-order:8003"},
        "/payments":  {"backend": "http://fastcart-payment:8005"},
        "/reviews":  {"backend": "http://fastcart-review:8006"}
    }
}

import os

GATEWAY_SECRET = os.environ.get("GATEWAY_SECRET")
if not GATEWAY_SECRET:
    raise RuntimeError("GATEWAY_SECRET environment variable is not set!")

def add_security_headers(headers: dict) -> dict:
    headers["X-XSS-Protection"] = "1; mode=block"
    headers["X-Frame-Options"] = "DENY"
    headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    headers["Pragma"] = "no-cache"
    return headers



@app.get("/docs", include_in_schema=False)
async def gateway_swagger_ui(request: Request):
    base_url = str(request.base_url).rstrip("/")

    urls = [
        {"name": "Products API", "url": f"{base_url}/openapi/products.json"},
        {"name": "Orders API",   "url": f"{base_url}/openapi/orders.json"},
        {"name": "Auth API",     "url": f"{base_url}/openapi/auth.json"},
        {"name": "Payment API",  "url": f"{base_url}/openapi/payments.json"},
        {"name": "Reviews API",  "url": f"{base_url}/openapi/reviews.json"}
    ]
    urls_json = json.dumps(urls)

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>FastCart API Gateway</title>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" type="text/css"
          href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
<div id="swagger-ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-standalone-preset.js"></script>
<script>
window.onload = function() {{
    const ui = SwaggerUIBundle({{
        urls: {urls_json},
        "urls.primaryName": "Products API",
        dom_id: "#swagger-ui",
        presets: [
            SwaggerUIBundle.presets.apis,
            SwaggerUIStandalonePreset
        ],
        layout: "StandaloneLayout"
    }})
    window.ui = ui
}}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)



@app.get("/openapi/{service_name}.json", include_in_schema=False)
async def get_openapi_schema(service_name: str):
    backend_url = GATEWAY_CONFIG["endpoints"].get(f"/{service_name}", {}).get("backend")
    if not backend_url:
        raise HTTPException(status_code=404, detail="Service not found")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(f"{backend_url}/openapi.json")
            return Response(content=response.content, media_type="application/json")
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail=f"{service_name} docs unavailable")



@app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def route(request: Request, path_name: str):
    request_path = f"/{path_name}"

    matched_backend = None
    for prefix, config in GATEWAY_CONFIG["endpoints"].items():
        if request_path.startswith(prefix):
            matched_backend = config["backend"]
            break

    if not matched_backend:
        raise HTTPException(status_code=404, detail="No such route in Gateway Config")

    target_url = f"{matched_backend}{request_path}"

    headers = dict(request.headers)
    headers.pop("host", None)
    headers["gateway-jwt-token"] = GATEWAY_SECRET

    body = await request.body()

    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
                timeout=10.0,
            )
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Backend service unavailable")

    response_headers = dict(response.headers)
    response_headers.pop("content-encoding", None)
    response_headers.pop("content-length", None)
    response_headers = add_security_headers(response_headers)

    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=response_headers,
    )