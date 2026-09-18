"""ThermaSight — entry point.

Run:  python run.py            (serves http://localhost:8000)
      python run.py --port 9000
"""
import sys
import uvicorn

def main() -> None:
    import os

    port = int(os.environ.get("PORT", "8000"))
    if "--port" in sys.argv:
        i = sys.argv.index("--port")
        try:
            port = int(sys.argv[i + 1])
        except (IndexError, ValueError):
            pass
    uvicorn.run("backend.main:app", host="127.0.0.1", port=port, log_level="info")

if __name__ == "__main__":
    main()