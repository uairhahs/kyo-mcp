# Testing MCP with curl

## Make a session

```bash
❯ curl -N -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"link_kyo_nodes","arguments":{"source_id":"kyo-node-0001","target_id":"kyo-node-0002","relation_type":"references"}}}'
{"jsonrpc":"2.0","id":"server-error","error":{"code":-32600,"message":"Session not found"}}[ble: EOF]                                         
❯ SESSION_ID=$(curl -s -D - -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl-test","version":"0.0.1"}}}' \
  -o /dev/null | grep -i mcp-session-id | tr -d '\r' | cut -d' ' -f2)

echo "Session ID: $SESSION_ID"
```

## use session to pass subsequent curl

```bash
curl -N -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"create_kyo_node","arguments":{"title":"Test 2","description":"A sanity-check for linking nodes","tags":["test"]}}}'
```
