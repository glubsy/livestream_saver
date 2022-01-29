FROM python:3.8.5-alpine

RUN apk update \
    && apk add --virtual build-deps gcc python3-dev musl-dev \
    && apk add postgresql \
    && apk add postgresql-dev \
    && pip install psycopg2 \
    && apk add jpeg-dev zlib-dev libjpeg \
    && pip install Pillow \
    && apk del build-deps

COPY ./requirements.txt requirements.txt
COPY ./livestream_saver.py livestream_saver.py
COPY ./livestream_saver livestream_saver
RUN python3 -m pip install --upgrade pip
RUN python3 -m pip install -r requirements.txt

COPY ./livestream_saver.cfg.template ./config.yml
RUN chmod a+x livestream_saver.py
