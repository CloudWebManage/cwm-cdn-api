FROM nginx:alpine@sha256:42a516af16b852e33b7682d5ef8acbd5d13fe08fecadc7ed98605ba5e3b26ab8
RUN apk update && apk add python3
COPY entrypoint.py /docker-entrypoint.d/999-tenant-nginx-py.sh
