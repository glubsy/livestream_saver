FROM python:3.8.5-alpine

RUN apk update \
    && apk add --virtual build-deps gcc python3-dev musl-dev \
    && apk add postgresql \
    && apk add postgresql-dev \
    && pip install psycopg2 \
    && apk add jpeg-dev zlib-dev libjpeg \
    && pip install Pillow \
    && apk del build-deps

COPY ./requirements.txt /app/requirements.txt
COPY ./livestream_saver.py /app/livestream_saver.py
COPY ./livestream_saver /app/livestream_saver
RUN python3 -m pip install --upgrade pip
RUN python3 -m pip install -r /app/requirements.txt

ENV PATH "$PATH:/app/"
COPY ./livestream_saver.cfg.template ./livestream_saver.cfg
RUN chmod u+x /app/livestream_saver.py
RUN ln -s /app/livestream_saver.py /usr/bin
