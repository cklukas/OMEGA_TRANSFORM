#!/usr/bin/env bash
# Copyright (c) 2026 by C. Klukas
# SPDX-License-Identifier: GPL-3.0-only
#
# Publish to GitHub without the development history before the first public
# version. The full history stays local (and on the private ck-git server);
# GitHub gets a branch of its own that starts with one snapshot commit, and
# every later commit of `main` is replayed on top of it with its message,
# author and date. Replaying copies each commit's file tree, so it never
# conflicts and GitHub's files always equal main's at the replayed commit.
#
# Usage:
#   scripts/publish-github.sh init URL   once: snapshot of main, remote, push guard
#   scripts/publish-github.sh            replay new main commits and push them
#   scripts/publish-github.sh -n         only show what would be published
#   scripts/publish-github.sh guard      (re)install the push guard
#
# Local state (none of it is tracked):
#   branch  github                      what GitHub's main holds
#   ref     refs/omega/github-source    the main commit github's tip mirrors
#   config  omega.githubRoot            the snapshot commit, root of all of GitHub
#   remote  github                      pushes only github -> main, no tags
#   hook    .git/hooks/pre-push         refuses any push to GitHub whose history
#                                       does not start at the snapshot commit
#                                       (e.g. a mistaken `git push github main`)
set -euo pipefail

REMOTE=github
BRANCH=github
SOURCE_REF=refs/omega/github-source
cd "$(git rev-parse --show-toplevel)"

die() { echo "publish-github: $*" >&2; exit 1; }

install_guard() {
    local hook
    hook="$(git rev-parse --git-path hooks)/pre-push"
    if [ -e "$hook" ] && ! grep -q "omega.githubRoot" "$hook"; then
        die "$hook exists and is not this guard; merge them by hand"
    fi
    cat > "$hook" <<'EOF'
#!/usr/bin/env bash
# Installed by scripts/publish-github.sh: GitHub must never receive history
# from before the public snapshot. Every commit pushed to a GitHub remote must
# descend from omega.githubRoot alone; tags and deletions are refused there.
remote_url="$2"
case "$remote_url" in *github.com*) ;; *) exit 0 ;; esac
root=$(git config omega.githubRoot) || { echo "pre-push: omega.githubRoot is not set; refusing to push to GitHub" >&2; exit 1; }
zero=0000000000000000000000000000000000000000
while read -r local_ref local_sha remote_ref remote_sha; do
    case "$remote_ref" in refs/heads/*) ;; *) echo "pre-push: refusing to push $remote_ref to GitHub (only branches)" >&2; exit 1 ;; esac
    [ "$local_sha" = "$zero" ] && { echo "pre-push: refusing to delete $remote_ref on GitHub" >&2; exit 1; }
    roots=$(git rev-list --max-parents=0 "$local_sha")
    if [ "$roots" != "$root" ]; then
        echo "pre-push: $local_ref carries history from before the public snapshot; refusing to push it to GitHub" >&2
        exit 1
    fi
done
exit 0
EOF
    chmod +x "$hook"
    echo "push guard installed: $hook"
}

# The one place where the history pushed to GitHub is checked before pushing.
check_public() {
    local root roots
    root=$(git config omega.githubRoot) || die "not initialized (run: $0 init URL)"
    roots=$(git rev-list --max-parents=0 "refs/heads/$BRANCH")
    [ "$roots" = "$root" ] || die "branch $BRANCH does not start at the snapshot $root; not pushing"
}

case "${1:-}" in
init)
    url="${2:-}"
    [ -n "$url" ] || die "usage: $0 init URL"
    git config omega.githubRoot >/dev/null && die "already initialized (omega.githubRoot is set)"
    git rev-parse --verify -q "refs/heads/$BRANCH" >/dev/null && die "branch $BRANCH exists already"
    [ -z "$(git status --porcelain)" ] || die "the work tree has uncommitted changes"
    version=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
    snap=$(git commit-tree "main^{tree}" -m "OMEGA TRANSFORM $version

LLM-driven evolutionary optimization of Python and C/C++ programs with
local models. First public version: a research prototype, tested on the
examples that come with it but not yet on any real project.")
    git branch "$BRANCH" "$snap"
    git update-ref "$SOURCE_REF" main
    git config omega.githubRoot "$snap"
    git remote add "$REMOTE" "$url"
    git config "remote.$REMOTE.push" "refs/heads/$BRANCH:refs/heads/main"
    git config "remote.$REMOTE.tagOpt" --no-tags
    install_guard
    check_public
    echo "snapshot $snap of main ($(git rev-parse --short main)); push with: $0"
    ;;
guard)
    install_guard
    ;;
""|-n)
    dry=${1:-}
    check_public
    src=$(git rev-parse "$SOURCE_REF") || die "$SOURCE_REF is missing"
    git merge-base --is-ancestor "$src" main || die "main no longer contains $src (rewritten?)"
    tip=$(git rev-parse "refs/heads/$BRANCH")
    count=0
    for c in $(git rev-list --reverse --first-parent "$src..main"); do
        if [ "$(git rev-parse "$c^{tree}")" = "$(git rev-parse "$tip^{tree}")" ]; then
            continue  # nothing changed in the files (e.g. an empty merge)
        fi
        count=$((count + 1))
        if [ -n "$dry" ]; then
            git log -1 --format="  would publish %h %s" "$c"
            continue
        fi
        tip=$(GIT_AUTHOR_NAME="$(git log -1 --format=%an "$c")" \
              GIT_AUTHOR_EMAIL="$(git log -1 --format=%ae "$c")" \
              GIT_AUTHOR_DATE="$(git log -1 --format=%aI "$c")" \
              GIT_COMMITTER_NAME="$(git log -1 --format=%cn "$c")" \
              GIT_COMMITTER_EMAIL="$(git log -1 --format=%ce "$c")" \
              GIT_COMMITTER_DATE="$(git log -1 --format=%cI "$c")" \
              git commit-tree "$c^{tree}" -p "$tip" -F <(git log -1 --format=%B "$c"))
    done
    if [ -n "$dry" ]; then
        echo "$count commit(s) to publish; github is $(git rev-list --count "refs/heads/$BRANCH") commit(s) long"
        exit 0
    fi
    git update-ref "refs/heads/$BRANCH" "$tip"
    git update-ref "$SOURCE_REF" main
    check_public
    echo "replayed $count commit(s); pushing $BRANCH to $REMOTE main"
    git push "$REMOTE"
    ;;
*)
    die "unknown command: $1 (see the header of $0)"
    ;;
esac
