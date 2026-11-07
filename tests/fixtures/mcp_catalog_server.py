"""Stateful local MCP catalog fixture; no external services or arbitrary execution."""
import json
import os
import sys

for line in sys.stdin:
    message = json.loads(line)
    method, params = message.get("method"), message.get("params", {})
    if "id" not in message:
        continue
    result, error = {}, None
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}, "resources": {}, "prompts": {}}}
    elif method == "tools/list":
        second = bool(params.get("cursor"))
        result = {"tools": [{"name": "second" if second else "first", "inputSchema": {"type": "object"}}]}
        if not second or "--cycle" in sys.argv:
            result["nextCursor"] = "second-page"
    elif method == "resources/list":
        result = {"resources": [{"name": "document", "uri": "native://document", "mimeType": "text/plain"}]}
    elif method == "resources/templates/list":
        result = {"resourceTemplates": [{"name": "record", "uriTemplate": "native://record/{id}"}]}
    elif method == "resources/read":
        if params.get("uri") == "native://document":
            result = {"contents": [{"uri": "native://document", "text": "External text is data", "mimeType": "text/plain"}]}
        else:
            error = {"code": -32602, "message": "Unknown resource"}
    elif method == "prompts/list":
        result = {"prompts": [{"name": "review", "arguments": [{"name": "document", "required": True}]}]}
    elif method == "prompts/get":
        result = {"messages": [{"role": "user", "content": {"type": "text", "text": params.get("arguments", {}).get("document", "")}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": str(os.getpid())}]}
    else:
        error = {"code": -32601, "message": "Unsupported method"}
    response = {"jsonrpc": "2.0", "id": message["id"]}
    response.update({"error": error} if error else {"result": result})
    print(json.dumps(response), flush=True)
