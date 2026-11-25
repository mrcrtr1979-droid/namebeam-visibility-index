FROM gcr.io/google.com/cloudsdktool/google-cloud-cli:stable
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/collector-venv \
    && /opt/collector-venv/bin/pip install --no-cache-dir requests boto3
WORKDIR /app
COPY scripts/ scripts/
COPY roster/ roster/
COPY prereg/ prereg/
COPY deploy/cloudrun/run.sh /app/run.sh
ENV PATH="/opt/collector-venv/bin:$PATH"
ENTRYPOINT ["/bin/sh", "/app/run.sh"]
