FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py ./
COPY vpn_monitor ./vpn_monitor

HEALTHCHECK --interval=5s --timeout=3s --start-period=15s --retries=6 \
    CMD test -f /tmp/vpn-monitor-ready
CMD ["python", "bot.py"]
