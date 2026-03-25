"""Allow running as: python -m mcp_server [--web]"""

import sys

if "--web" in sys.argv:
    import uvicorn
    from .web import app

    port = 8188
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    print(f"Comfy Cloud Studio → http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
else:
    from .server import main
    main()
