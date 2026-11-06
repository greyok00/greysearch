#!/usr/bin/env python3
import sys
from pathlib import Path
ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT)
if __name__ == '__main__':
    import portal_server
    import portal_ledger
    portal_ledger.register(portal_server.app)
    portal_server.app.run(host=portal_server.HOST, port=portal_server.PORT, threaded=True)
