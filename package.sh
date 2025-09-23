#!/bin/bash

set -ex

SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"
cd "$SCRIPTPATH"

rm -rf "${SCRIPTPATH}/build"
mkdir -p "${SCRIPTPATH}/build/bin"

#
# Build statically-linked pciutils 'lspci' binary and pci.ids.gz
#

pushd extern/pciutils
git clean -dfx
git reset --hard HEAD
make -j$(nproc) update-pciids lspci OPT=-Os IDSDIR="./hwdata" LIBKMOD=no DNS=no HWDB=no ZLIB=yes SHARED=no CC="cc -static -flto"
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

install -Dm0755 debug_report.py "${SCRIPTPATH}/build/edera-debug-report"

#
# Install documentation
#
install -Dm0644 README-customer.md "${SCRIPTPATH}/build/README.md"

#
# Package prefix as tarball
#
VERSION=$(git describe --tags)
PKG_NAME="edera-debug-report-${VERSION}"
mkdir -p out
rm -rf "out/${PKG_NAME}"
cp -R build "out/${PKG_NAME}"
pushd out
tar czf "${PKG_NAME}.tar.gz" "${PKG_NAME}"
popd
rm -rf "out/${PKG_NAME}"
