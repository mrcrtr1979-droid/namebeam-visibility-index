FROM gcr.io/google.com/cloudsdktool/google-cloud-cli:stable
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-requests python3-boto3 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY scripts/ scripts/
COPY roster/ roster/
COPY prereg/ prereg/
COPY deploy/cloudrun/run.sh /app/run.sh
ENTRYPOINT ["/bin/sh", "/app/run.sh"]
