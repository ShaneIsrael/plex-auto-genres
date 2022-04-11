FROM python:3.9-alpine

RUN apk add --no-cache \
    gcc \
    g++ \
    musl-dev

COPY plex-auto-genres.py /
COPY automate.py /
COPY src/ /src/
COPY requirements.txt /
COPY docker/entrypoint.sh /

RUN pip3 install -r requirements.txt
RUN chmod +x /entrypoint.sh
RUN echo "0 1 * * * cd / && python3 -u /automate.py" > /etc/crontabs/root

ENTRYPOINT ["/entrypoint.sh"]
