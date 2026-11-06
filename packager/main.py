import os
import sys
from pathlib import Path

def _resolve_lib():
    here = Path(__file__).resolve().parent
    if (here / 'doctor.cpython-313-x86_64-linux-gnu.so').is_file():
        return here
    override = os.environ.get('GREYSEARCH_LIB')
    if override and (Path(override) / 'doctor.cpython-313-x86_64-linux-gnu.so').is_file():
        return Path(override)
    sys.exit('Cannot locate compiled lib dir: packager/main.py is not inside it and GREYSEARCH_LIB is not set (or does not contain the compiled modules).')
sys.path.insert(0, str(_resolve_lib()))

def main():
    if len(sys.argv) < 2:
        print('Usage: python3 packager/main.py <command> [args...]')
        print('Commands: doctor, research, portal, build-site, mcp')
        return 1
    cmd = sys.argv[1]
    if cmd == 'doctor':
        import doctor
        return doctor.main()
    elif cmd == 'research':
        import research
        research.main()
        return 0
    elif cmd == 'portal':
        import portal_server
        portal_server.app.run(host=portal_server.HOST, port=portal_server.PORT, threaded=True)
        return 0
    elif cmd == 'build-site':
        import greysearch_site_builder
        return greysearch_site_builder.render()
    elif cmd == 'mcp':
        import greysearch_mcp
        greysearch_mcp.main()
        return 0
    else:
        print(f'Unknown command: {cmd}')
        return 1
if __name__ == '__main__':
    sys.exit(main())
