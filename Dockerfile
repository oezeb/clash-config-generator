FROM python:3-alpine

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY main.py ./
COPY config.yaml ./
COPY clash-config.yaml ./

CMD [ "python", "./main.py" ]