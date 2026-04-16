#!/bin/bash

set -ex

SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"
cd "$SCRIPTPATH"

./build.sh

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
