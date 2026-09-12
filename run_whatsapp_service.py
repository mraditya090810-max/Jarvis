"""
run_whatsapp_service.py — convenience entry point so you don't have to
remember the uvicorn module path.

    python run_whatsapp_service.py

Reads PORT from the environment (default 8080) so cloud hosts that inject
their own PORT (Render, Railway, Fly.io, Heroku-style buildpacks, etc.)
work without any code changes.
"""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run("whatsapp_service.webhook:app", host="0.0.0.0", port=port, reload=False)
