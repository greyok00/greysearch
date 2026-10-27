import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    import portal_server
    portal_server.app.run(host=portal_server.HOST, port=portal_server.PORT, threaded=True)