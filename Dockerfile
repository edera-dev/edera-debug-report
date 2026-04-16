# Using Rocky Linux 9 as a proxy for RHEL9, which is the likely oldest
# supported Linux distro in use by enterprises out there.
FROM rockylinux:9 AS builder

RUN cat <<'EOF' >> /etc/dnf/dnf.conf
install_weak_deps=False
fastestmirror=True
max_parallel_downloads=8
EOF

RUN dnf update -y
RUN dnf install --enablerepo=crb -y gcc gcc-c++ make glibc-devel zlib-devel git glibc-static zlib-static

COPY . /workspace
WORKDIR /workspace

RUN /workspace/package.sh

#
# Create OCI image for Edera Protect Installer
#
FROM scratch AS edera-debug-report-oci
COPY --from=builder /workspace/build /var/lib/edera/protect/support

# vim: set ts=4 sts=4 sw=4 et:
