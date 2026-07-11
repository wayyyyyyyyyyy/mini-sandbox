from fastapi.testclient import TestClient

from app.main import app


def test_openapi_documents_json_api_response_wrapper():
    client = TestClient(app)

    schema = client.get("/openapi.json").json()
    context_response = schema["paths"]["/context"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]

    assert context_response["$ref"] == "#/components/schemas/Response_SandboxContext_"
    wrapper_schema = schema["components"]["schemas"]["Response_SandboxContext_"]
    assert set(wrapper_schema["properties"]) == {"success", "message", "data", "hint"}
    assert wrapper_schema["properties"]["data"]["$ref"] == "#/components/schemas/SandboxContext"


def test_openapi_documents_error_wrapper():
    client = TestClient(app)

    schema = client.get("/openapi.json").json()
    read_responses = schema["paths"]["/file/read"]["post"]["responses"]

    assert read_responses["404"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/SandboxResponse"
    assert read_responses["422"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/SandboxResponse"


def test_openapi_documents_file_download_as_binary_stream():
    client = TestClient(app)

    schema = client.get("/openapi.json").json()
    response = schema["paths"]["/file/download"]["get"]["responses"]["200"]

    assert response["content"]["application/octet-stream"]["schema"] == {
        "type": "string",
        "format": "binary",
    }


def test_openapi_documents_shell_websocket_extension():
    client = TestClient(app)

    schema = client.get("/openapi.json").json()
    websocket = schema["x-websockets"]["/shell/ws"]

    assert websocket["auth"] == ["X-Sandbox-Api-Key", "Authorization: Bearer", "ticket"]
    assert {"type": "input", "data": "ls -la\n"} in websocket["client_messages"]
    assert {"type": "output", "data": "..."} in websocket["server_messages"]


def test_openapi_excludes_raw_proxy_routes():
    client = TestClient(app)

    schema = client.get("/openapi.json").json()

    assert not any(path.startswith("/proxy/") or path == "/proxy/{port}" for path in schema["paths"])


def test_openapi_documents_browser_page_wrappers_and_mcp_image_content():
    client = TestClient(app)

    schema = client.get("/openapi.json").json()
    navigate_response = schema["paths"]["/browser/page/navigate"]["post"]["responses"]["200"]
    image_schema = schema["components"]["schemas"]["McpContentItem"]

    assert navigate_response["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/Response_BrowserNavigateResult_"
    )
    assert image_schema["properties"]["type"]["enum"] == ["text", "json", "image"]
    assert {item.get("type") for item in image_schema["properties"]["mimeType"]["anyOf"]} == {"string", "null"}
