import json, sys, time
initialized = False
counter = 0
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        time.sleep(.03)
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
    elif method == "notifications/initialized":
        initialized = True
        continue
    elif not initialized:
        raise SystemExit(2)
    elif method == "tools/call":
        counter += 1
        result = {"counter": counter}
        if msg.get("params", {}).get("name") == "uncertain":
            open("effect.txt", "w").write(str(counter))
            time.sleep(10)
        if msg.get("params", {}).get("name") == "flood":
            print("x" * 1000001, flush=True)
            continue
    else:
        result = {"tools": [{"name": "counter"}]}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
