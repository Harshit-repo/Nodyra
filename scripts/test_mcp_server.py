"""Minimal MCP test server using stdlib only. Two tools for MS3 3C E2E verification."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

TOOLS = [
    {
        "name": "echo",
        "description": "Echo back the input message",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The message to echo"}
            },
            "required": ["message"],
        },
    },
    {
        "name": "add",
        "description": "Add two numbers together",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "First number"},
                "b": {"type": "number", "description": "Second number"},
            },
            "required": ["a", "b"],
        },
    },
]


class MCPHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        method = body.get("method", "")
        req_id = body.get("id")

        if method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = body.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            if tool_name == "echo":
                result = {"content": [{"type": "text", "text": f"Echo: {arguments.get('message', '')}"}]}
            elif tool_name == "add":
                a = float(arguments.get("a", 0))
                b = float(arguments.get("b", 0))
                result = {"content": [{"type": "text", "text": f"Sum: {a + b}"}]}
            else:
                self._send_error(req_id, -32601, f"Unknown tool: {tool_name}")
                return
        elif method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-mcp-server", "version": "1.0.0"},
            }
        else:
            self._send_error(req_id, -32601, f"Unknown method: {method}")
            return

        self._send_ok(req_id, result)

    def _send_ok(self, req_id, result):
        resp = {"jsonrpc": "2.0", "id": req_id, "result": result}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp).encode())

    def _send_error(self, req_id, code, message):
        resp = {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp).encode())

    def log_message(self, *args):
        pass  # silence logs


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 9999), MCPHandler)
    print("MCP test server on http://127.0.0.1:9999")
    server.serve_forever()
