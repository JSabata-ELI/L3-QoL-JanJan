import requests

base = "http://10.78.0.64:8080/l3btproxy/archive-access/api/1.0"


for ep in [
    "",
    "/",
    "/api",
    "/api/",
    "/api/1.0",
    "/swagger",
    "/swagger-ui",
    "/openapi.json",
    "/v3/api-docs",
]:
    r = requests.get(base + ep, timeout=10)
    print(ep, r.status_code)