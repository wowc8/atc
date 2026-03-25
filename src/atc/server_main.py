"""Sidecar entry point — starts uvicorn server."""
import multiprocessing
import uvicorn

if __name__ == "__main__":
    multiprocessing.freeze_support()
    uvicorn.run("atc.api.app:create_app", factory=True, host="127.0.0.1", port=8420, log_level="info")
