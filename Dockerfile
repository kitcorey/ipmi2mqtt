FROM python:3.12-alpine
WORKDIR /app
COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

#COPY config.yaml config.yaml
COPY ipmi2mqtt.py ipmi2mqtt.py

#RUN apk add --no-cache curl jq wget \
#RUN apk add --no-cache curl jq wget tcpdump busybox avahi open-lldp openrc bash
#ENV INITSYSTEM on

ENTRYPOINT ["python3", "-u", "ipmi2mqtt.py"]
