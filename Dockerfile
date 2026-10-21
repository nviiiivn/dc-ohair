# D.C. O'HAIR hub. See docs/setup.md.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client sshpass ieee-data \
 && apt-get clean && find /var/lib/apt/lists -type f -delete

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent.py collect.py hub.py demo.py push.py discover.py ./
COPY web ./web

# the hub runs as an ordinary user; /data holds scans, history and the hub's ssh key
RUN useradd --create-home --uid 1000 dc && mkdir -p /data /config && chown dc:dc /data /config
USER dc
ENV DC_DATA=/data DC_CONFIG=/config/devices.yml DC_SSH_DIR=/data/ssh DC_HOSTFS=/hostfs PYTHONUNBUFFERED=1
EXPOSE 7171
VOLUME ["/data"]
HEALTHCHECK --interval=1m --timeout=5s CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7171/api/me', timeout=4)"
CMD ["python3", "hub.py"]
