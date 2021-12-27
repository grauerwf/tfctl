FROM alpine:3.7
MAINTAINER SchwarzW01f <schwarzw01f@i.ua>

RUN apk --update add openssh python3 git &&  \
    pip3 install --no-cache-dir --upgrade pip &&  \
    pip3 install --no-cache-dir --upgrade tfctl &&  \
    rm -rf /var/cache/apk/*