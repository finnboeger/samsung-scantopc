FROM --platform=linux/amd64 debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive

# Prevent post-install scripts from trying to start/restart services
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d \
    && chmod +x /usr/sbin/policy-rc.d

# Core runtime dependencies
RUN echo "deb http://deb.debian.org/debian trixie-backports main" > /etc/apt/sources.list.d/trixie-backports.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-pil \
    python3-sane \
    python3-pypdf \
    python3-six \
    python3-zeroconf \
    sane-utils \
    avahi-daemon \
    avahi-utils \
    dbus \
    libcups2 \
    curl \
    ca-certificates \
    gnupg && \
    apt install -y --no-install-recommends -t trixie-backports python3-pysnmp && \
    rm -rf /var/lib/apt/lists/*

# Add the bchemnet SULDR apt repository and install the Samsung driver
RUN echo "deb https://www.bchemnet.com/suldr/ debian extra" \
        > /etc/apt/sources.list.d/suldr.list \
    && apt-get update --allow-insecure-repositories \
    && apt-get install -y --no-install-recommends --allow-unauthenticated \
        suld-driver2-1.00.39hp \
    && rm -rf /var/lib/apt/lists/*

# Re-enable service startup (no longer needed during build, clean up)
RUN rm -f /usr/sbin/policy-rc.d

# Download the scanner server script from GitHub
RUN curl -fsSL \
    https://raw.githubusercontent.com/kleest/samsung-scantopc/main/samsungScannerServer.py \
    -o /usr/local/bin/samsungScannerServer.py \
    && chmod +x /usr/local/bin/samsungScannerServer.py

# Output directory for scanned files
RUN mkdir -p /scans

# Create a non-root user matching the default OWNER_UID
RUN useradd -u 1000 -m -s /bin/bash -g scanner scanuser \
    && mkdir -p /scans \
    && chown scanuser:scanner /scans

# Copy configuration files
COPY etc/avahi-daemon.conf /etc/avahi/avahi-daemon.conf
COPY etc/samsungScannerServer.conf /etc/samsungScannerServer.conf
COPY start.sh /start.sh
RUN chmod +x /start.sh

VOLUME ["/scans"]

# Samsung Scan-to-PC listener port
EXPOSE 9400

CMD ["/start.sh"]
