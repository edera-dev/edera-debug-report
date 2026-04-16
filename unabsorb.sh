#!/usr/bin/env bash
set -euo pipefail

super_top="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "error: not inside a git worktree" >&2
    exit 1
}

cd "${super_top}"

# Collect all submodule paths first, recursively, then process deepest-first.
mapfile -t submodules < <(
    git submodule foreach --quiet --recursive 'printf "%s\n" "$displaypath"' \
    | awk '{ depth=gsub(/\//, "/"); printf "%d\t%s\n", depth, $0 }' \
    | sort -r -n -k1,1 \
    | cut -f2-
)

if [[ ${#submodules[@]} -eq 0 ]]; then
    echo "No submodules found."
    exit 0
fi

for path in "${submodules[@]}"; do
    echo "Processing ${path}"

    cd "${super_top}/${path}"

    if [[ ! -f .git ]]; then
        echo "  skipping: .git is not a gitfile stub"
        continue
    fi

    gitfile="$(< .git)"
    case "${gitfile}" in
        "gitdir: "*)
            gitdir="${gitfile#gitdir: }"
            ;;
        *)
            echo "  skipping: .git is not a recognized gitfile stub"
            continue
            ;;
    esac

    if [[ "${gitdir}" = /* ]]; then
        src_gitdir="${gitdir}"
    else
        src_gitdir="$(realpath -m "${PWD}/${gitdir}")"
    fi

    if [[ ! -d "${src_gitdir}" ]]; then
        echo "  error: source gitdir does not exist: ${src_gitdir}" >&2
        exit 1
    fi

    rm -f .git
    mv "${src_gitdir}" .git

    # Remove core.worktree directly from config to avoid Git trying to
    # resolve the stale absorbed worktree path during `git config`.
    if [[ -f .git/config ]]; then
        python3 - "$PWD/.git/config" <<'PY'
import configparser
import io
import os
import sys

path = sys.argv[1]

cp = configparser.RawConfigParser()
cp.optionxform = str

with open(path, "r", encoding="utf-8") as f:
    data = f.read()

cp.read_file(io.StringIO(data))

changed = False
if cp.has_section("core") and cp.has_option("core", "worktree"):
    cp.remove_option("core", "worktree")
    changed = True
    if not cp.items("core"):
        cp.remove_section("core")

if changed:
    with open(path, "w", encoding="utf-8") as f:
        cp.write(f)
PY
    fi

    echo "  moved ${src_gitdir} -> ${PWD}/.git"
done
