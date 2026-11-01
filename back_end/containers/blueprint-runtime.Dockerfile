FROM python:3.14-slim

RUN apt-get update \
    && apt-get install --no-install-recommends -y libseccomp2 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pydantic==2.12.5

USER 65534:65534
WORKDIR /tmp
