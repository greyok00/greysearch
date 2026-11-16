FROM python:3.13-slim-bookworm

# Install system dependencies for chrome sandbox
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libgbm-dev \
    libxss1 \
    libasound2 \
    libxtst6 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy source
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install chromium for patchright (so first run doesn't need to download)
RUN python -m patchright install chromium

# Create a wrapper script to expose commands
RUN echo '#!/bin/bash\nset -e\nif [ "$#" -eq 0 ]; then\n    echo "Usage: $0 {doctor|research|portal|build-site|mcp} [args...]"\n    exit 1\nfi\nCOMMAND="$1"\nshift\ncase "$COMMAND" in\n    doctor)\n        exec python bin/greysearch-doctor.py "$@" ;;\n    research)\n        exec python bin/greysearch-research.py "$@" ;;\n    portal)\n        exec python bin/greysearch-portal.py "$@" ;;\n    "build-site")\n        exec python bin/greysearch-build-site.py "$@" ;;\n    mcp)\n        exec python bin/greysearch-mcp.py "$@" ;;\n    *)\n        echo "Unknown command: $COMMAND"\n        echo "Available: doctor, research, portal, build-site, mcp"\n        exit 1\n    esac\n' > /usr/local/bin/greysearch && chmod +x /usr/local/bin/greysearch

ENTRYPOINT ["/usr/local/bin/greysearch"]
