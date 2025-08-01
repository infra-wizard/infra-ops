FROM ubuntu:22.04

# Set build arguments for user configuration
ARG COVERITY_UID=1001
ARG COVERITY_GID=121
ARG COVERITY_USER=runner

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Update package list and install essential packages for GitHub Actions
RUN apt-get update && apt-get install -y \
    sudo \
    curl \
    wget \
    git \
    vim \
    unzip \
    tar \
    build-essential \
    software-properties-common \
    apt-transport-https \
    ca-certificates \
    gnupg \
    lsb-release \
    python3 \
    python3-pip \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Create runner group if it doesn't exist (GitHub Actions compatible)
RUN groupadd -g ${COVERITY_GID} ${COVERITY_USER} || true

# Create runner user with sudo privileges (GitHub Actions compatible)
RUN useradd -m -u ${COVERITY_UID} -g ${COVERITY_GID} -s /bin/bash ${COVERITY_USER} && \
    echo "${COVERITY_USER} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers && \
    usermod -aG sudo ${COVERITY_USER}

# Create common directories that runner user might need
RUN mkdir -p /opt/coverity /var/log/coverity /tmp/coverity /github/workspace && \
    chown -R ${COVERITY_USER}:${COVERITY_USER} /opt/coverity /var/log/coverity /tmp/coverity /github/workspace

# Set working directory to GitHub Actions workspace
WORKDIR /github/workspace

# Switch to coverity user
USER ${COVERITY_USER}

# Set environment variables
ENV USER=${COVERITY_USER}
ENV HOME=/home/${COVERITY_USER}

# Default command
CMD ["/bin/bash"]
