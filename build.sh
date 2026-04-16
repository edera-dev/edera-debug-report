#!/bin/bash

set -ex

SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"
cd "$SCRIPTPATH"

rm -rf "${SCRIPTPATH}/build"
mkdir -p "${SCRIPTPATH}/build/bin"

#
# Build statically-linked dmidecode binary
#
pushd extern/dmidecode
make -j$(nproc) dmidecode CC="gcc" CFLAGS="-Os -static -flto" LDFLAGS="-static -flto"
install -Dm0755 dmidecode "${SCRIPTPATH}/build/bin/dmidecode"
popd

#
# Build statically-linked pciutils 'lspci' binary and pci.ids.gz
#

pushd extern/pciutils
make -j$(nproc) update-pciids lspci OPT=-Os IDSDIR="./hwdata" LIBKMOD=no DNS=no HWDB=no ZLIB=yes SHARED=no RANLIB=gcc-ranlib AR=gcc-ar CC="gcc -static -flto"
mkdir hwdata
./update-pciids
install -Dm0755 update-pciids "${SCRIPTPATH}/build/bin/update-pciids"
install -Dm0755 lspci "${SCRIPTPATH}/build/bin/lspci"
install -Dm0644 hwdata/pci.ids.gz "${SCRIPTPATH}/build/hwdata/pci.ids.gz"
popd
strip -x "${SCRIPTPATH}/build/bin/lspci"

#
# Install script in prefix
#

install -Dm0755 edera-debug-report "${SCRIPTPATH}/build/edera-debug-report"

#
# Install documentation
#
install -Dm0644 README.md "${SCRIPTPATH}/build/README.md"
