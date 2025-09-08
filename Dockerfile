FROM debian:buster
RUN echo "deb http://archive.debian.org/debian buster main" > /etc/apt/sources.list && \
    echo "deb http://archive.debian.org/debian-security buster/updates main" >> /etc/apt/sources.list && \
    echo "Acquire::Check-Valid-Until false;" > /etc/apt/apt.conf.d/99no-check-valid-until

RUN apt update && \
    apt upgrade -y && \
    apt install -y openssl libssl1.1 ca-certificates curl

RUN echo "=== Packages depending on libdb5.3 ===" && \
    apt-cache rdepends libdb5.3 || true

RUN apt remove --purge -y libdb5.3 libdb5.3-dev libdb5.3-java libdb5.3-java-jni libdb5.3++ libdb5.3++-dev || true

RUN dpkg --remove --force-depends libdb5.3 || true

RUN apt autoremove --purge -y || true && \
    apt autoclean || true

RUN find /usr -name "*libdb5.3*" -delete 2>/dev/null || true && \
    find /lib -name "*libdb5.3*" -delete 2>/dev/null || true && \
    find /var -name "*libdb5.3*" -delete 2>/dev/null || true

RUN echo "=== Checking for remaining libdb5.3 packages ===" && \
    dpkg -l | grep libdb5.3 || echo "No libdb5.3 packages found"

RUN apt clean && \
    rm -rf /var/lib/apt/lists/*
RUN openssl version
