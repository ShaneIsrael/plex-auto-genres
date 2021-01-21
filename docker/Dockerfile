FROM python:3-alpine

RUN apk add --no-cache \
    gcc \
    musl-dev

COPY plex-auto-genres.py /
COPY automate.py /
COPY src/ /src/
COPY requirements.txt /
COPY docker/entrypoint.sh /

RUN pip3 install -r requirements.txt
RUN chmod +x /entrypoint.sh
RUN echo "0 1 * * * python3 /automate.py" > /etc/crontabs/root

ENTRYPOINT ["/entrypoint.sh"]