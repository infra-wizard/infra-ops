FROM debian:buster

RUN echo "deb http://archive.debian.org/debian buster main" > /etc/apt/sources.list && \
    echo "deb http://archive.debian.org/debian-security buster/updates main" >> /etc/apt/sources.list && \
    echo "Acquire::Check-Valid-Until false;" > /etc/apt/apt.conf.d/99no-check-valid-until

RUN apt update && \
    apt upgrade -y && \
    apt install -y openssl libssl1.1 ca-certificates curl

RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 python3-pip

# Check what packages depend on libdb5.3 before removal
RUN echo "=== Packages depending on libdb5.3 ===" && \
    apt-cache rdepends libdb5.3 || true

# Force remove libdb5.3 and all its dependents
RUN apt remove --purge -y libdb5.3 libdb5.3-dev libdb5.3-java libdb5.3-java-jni libdb5.3++ libdb5.3++-dev || true

# If libdb5.3 is still present, force remove it with --force-depends
RUN dpkg --remove --force-depends libdb5.3 || true

# Clean up orphaned packages
RUN apt autoremove --purge -y || true && \
    apt autoclean || true

# Force remove any remaining libdb files
RUN find /usr -name "*libdb5.3*" -delete 2>/dev/null || true && \
    find /lib -name "*libdb5.3*" -delete 2>/dev/null || true && \
    find /var -name "*libdb5.3*" -delete 2>/dev/null || true

# Check if libdb5.3 is still present
RUN echo "=== Checking for remaining libdb5.3 packages ===" && \
    dpkg -l | grep libdb5.3 || echo "No libdb5.3 packages found"

# Clean up
RUN apt clean && \
    rm -rf /var/lib/apt/lists/*

# Verify SSL functionality still works
RUN openssl version

# Add your application files here
# COPY . /app
# WORKDIR /app
