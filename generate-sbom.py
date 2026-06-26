#!/usr/bin/env python3
"""Generate a curated CycloneDX SBOM for the edera-debug-report-oci image.

The image is `FROM scratch` and ships, under /var/lib/edera/protect/support/:
  - bin/dmidecode            statically-linked C binary (submodule extern/dmidecode)
  - bin/lspci, update-pciids statically-linked C binaries (submodule extern/pciutils)
  - hwdata/pci.ids.gz         PCI id database bundled with pciutils
  - edera-debug-report        the stdlib-only Python driver script (this repo)
  - README.md

There is no package database to scan, so this is a curated SBOM describing what
actually ships. The C binaries are static, so glibc and zlib (linked in from the
Rocky Linux builder) are genuinely part of the shipped artifacts and are listed.

Reads from the environment (set by the build-oci workflow step):
  COMPONENT          image/component name (default: edera-debug-report-oci)
  REPO_COMMIT        this repo's commit (the edera-debug-report tool)
  REPO_VERSION       this repo's git-describe
  DMIDECODE_COMMIT   resolved commit of extern/dmidecode
  DMIDECODE_VERSION  git-describe of same
  PCIUTILS_COMMIT    resolved commit of extern/pciutils
  PCIUTILS_VERSION   git-describe of same
  GLIBC_VERSION      glibc-static version-release from the builder rpm DB (optional)
  ZLIB_VERSION       zlib-static version-release from the builder rpm DB (optional)

Writes <COMPONENT>.cdx.json (CycloneDX 1.6) in the current directory.
"""
import json
import os
import sys


def github_source(name, gh_path, commit, version, ctype="application"):
    """A GitHub-hosted source component, pinned to commit in its purl."""
    purl = "pkg:github/%s" % gh_path
    if commit:
        purl = "%s@%s" % (purl, commit)
    component = {
        "bom-ref": purl,
        "type": ctype,
        "name": name,
        "purl": purl,
        "externalReferences": [
            {"type": "vcs", "url": "https://github.com/%s.git" % gh_path}
        ],
    }
    if version:
        component["version"] = version
    return component


def generic_vcs_source(name, vcs_url, commit, version, ctype="application"):
    """A source that does not live on GitHub (e.g. git.savannah). purls for
    generic sources have no standard commit qualifier, so the commit is carried
    as a property and the version (if any) goes in the purl."""
    purl = "pkg:generic/%s" % name
    if version:
        purl = "%s@%s" % (purl, version)
    component = {
        "bom-ref": purl,
        "type": ctype,
        "name": name,
        "purl": purl,
        "externalReferences": [{"type": "vcs", "url": vcs_url}],
    }
    if version:
        component["version"] = version
    if commit:
        component["properties"] = [
            {"name": "dev.edera.source.commit", "value": commit}
        ]
    return component


def static_lib(name, version):
    """A library statically linked into the shipped C binaries (from the Rocky
    Linux builder). Part of the runtime artifact, so it is listed."""
    purl = "pkg:generic/%s@%s" % (name, version)
    return {
        "bom-ref": purl,
        "type": "library",
        "name": name,
        "version": version,
        "purl": purl,
        "properties": [
            {"name": "dev.edera.linkage", "value": "static"},
            {"name": "dev.edera.builder", "value": "rockylinux:9"},
        ],
    }


def build_components():
    components = [
        # The Python driver tool shipped by this repo.
        github_source(
            "edera-debug-report",
            "edera-dev/edera-debug-report",
            os.environ.get("REPO_COMMIT", ""),
            os.environ.get("REPO_VERSION", ""),
        ),
        # dmidecode lives on git.savannah.nongnu.org (not GitHub).
        generic_vcs_source(
            "dmidecode",
            "https://git.savannah.nongnu.org/git/dmidecode.git",
            os.environ.get("DMIDECODE_COMMIT", ""),
            os.environ.get("DMIDECODE_VERSION", ""),
        ),
        # pciutils ships lspci, update-pciids and the bundled pci.ids database.
        github_source(
            "pciutils",
            "pciutils/pciutils",
            os.environ.get("PCIUTILS_COMMIT", ""),
            os.environ.get("PCIUTILS_VERSION", ""),
        ),
    ]
    # glibc + zlib are statically linked into the C binaries (genuinely shipped).
    glibc = os.environ.get("GLIBC_VERSION", "")
    if glibc:
        components.append(static_lib("glibc", glibc))
    zlib = os.environ.get("ZLIB_VERSION", "")
    if zlib:
        components.append(static_lib("zlib", zlib))
    return components


def main():
    comp = os.environ.get("COMPONENT", "edera-debug-report-oci")
    version = os.environ.get("REPO_VERSION", "")
    components = build_components()

    image_ref = "%s@%s" % (comp, version) if version else comp
    metadata_component = {
        "bom-ref": image_ref,
        "type": "container",
        "name": comp,
    }
    if version:
        metadata_component["version"] = version

    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {"component": metadata_component},
        "components": components,
        "dependencies": [
            {
                "ref": image_ref,
                "dependsOn": [c["bom-ref"] for c in components if c.get("bom-ref")],
            }
        ],
    }

    with open("%s.cdx.json" % comp, "w") as out:
        json.dump(document, out, indent=2)
        out.write("\n")

    print(json.dumps({"component": comp, "components": len(components)}, indent=2))


if __name__ == "__main__":
    main()
