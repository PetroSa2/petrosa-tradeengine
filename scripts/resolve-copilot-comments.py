#!/usr/bin/env python3
"""
Resolves unresolved Copilot-started review threads on a GitHub PR.

Queries review threads via GitHub GraphQL, posts an "Addressed in commit <sha>"
reply on each unresolved Copilot thread, then calls resolveReviewThread to set
isResolved=true — allowing the copilot-review gate to pass without human clicks.

Only threads started by Copilot are resolved; human-started threads are skipped.

Usage:
    python scripts/resolve-copilot-comments.py <PR_URL_or_number> \
        [--repo OWNER/REPO] [--sha SHA] [--dry-run]

Examples:
    python scripts/resolve-copilot-comments.py \
        https://github.com/PetroSa2/petrosa-tradeengine/pull/364
    python scripts/resolve-copilot-comments.py 364 \
        --repo PetroSa2/petrosa-tradeengine --dry-run
"""

import argparse
import json
import re
import subprocess
import sys

COPILOT_LOGINS = {"copilot", "copilot-pull-request-reviewer"}

_THREADS_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      headRefOid
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          isOutdated
          comments(first: 1) {
            nodes {
              author { login }
            }
          }
        }
      }
    }
  }
}
"""

_REPLY_MUTATION = """
mutation($threadId: ID!, $body: String!) {
  addPullRequestReviewThreadReply(input: {
    pullRequestReviewThreadId: $threadId
    body: $body
  }) {
    comment { id }
  }
}
"""

_RESOLVE_MUTATION = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { isResolved }
  }
}
"""


def is_copilot_login(login: str) -> bool:
    if not login:
        return False
    lower = login.lower()
    return lower in COPILOT_LOGINS or lower.startswith("copilot-pull-request-reviewer[")


def gh_graphql(query: str, variables: dict) -> dict:
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, val in variables.items():
        if val is None:
            continue
        if isinstance(val, int):
            cmd += ["-F", f"{key}={val}"]
        else:
            cmd += ["-f", f"{key}={val}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print(
            "Error: 'gh' CLI not found. Install it from https://cli.github.com and run 'gh auth login'.",
            file=sys.stderr,
        )
        sys.exit(1)
    if result.returncode != 0:
        print(f"GraphQL error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    data = json.loads(result.stdout)
    if "errors" in data:
        print(
            f"GraphQL errors: {json.dumps(data['errors'], indent=2)}", file=sys.stderr
        )
        sys.exit(1)
    return data


def load_threads(owner: str, repo: str, pr: int) -> tuple[str, list[dict]]:
    threads: list[dict] = []
    cursor = None
    head_sha = None

    _PAGE_LIMIT = 20
    for page in range(_PAGE_LIMIT):
        variables: dict = {"owner": owner, "repo": repo, "pr": pr, "cursor": cursor}
        data = gh_graphql(_THREADS_QUERY, variables)
        pr_data = data["data"]["repository"]["pullRequest"]

        if head_sha is None:
            head_sha = pr_data["headRefOid"]

        conn = pr_data["reviewThreads"]
        for node in conn["nodes"]:
            comments = node.get("comments", {}).get("nodes", [])
            starter_login = None
            if comments:
                author = comments[0].get("author") or {}
                starter_login = author.get("login")
            threads.append(
                {
                    "id": node["id"],
                    "isResolved": node["isResolved"],
                    "isOutdated": node["isOutdated"],
                    "starterLogin": starter_login,
                }
            )

        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
        if page == _PAGE_LIMIT - 1:
            print(
                f"Error: exceeded {_PAGE_LIMIT}-page pagination limit while threads remain. "
                "Aborting to avoid silently missing unresolved threads.",
                file=sys.stderr,
            )
            sys.exit(1)

    return head_sha or "", threads


def post_reply(thread_id: str, body: str) -> None:
    gh_graphql(_REPLY_MUTATION, {"threadId": thread_id, "body": body})


def resolve_thread(thread_id: str) -> bool:
    data = gh_graphql(_RESOLVE_MUTATION, {"threadId": thread_id})
    return bool(data["data"]["resolveReviewThread"]["thread"]["isResolved"])


def parse_pr_ref(pr_arg: str, repo_arg: str | None) -> tuple[str, str, int]:
    url_match = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_arg)
    if url_match:
        return url_match.group(1), url_match.group(2), int(url_match.group(3))

    if pr_arg.isdigit():
        if not repo_arg:
            print(
                "Error: --repo OWNER/REPO is required when pr is a number",
                file=sys.stderr,
            )
            sys.exit(1)
        parts = repo_arg.split("/", 1)
        if len(parts) != 2:
            print("Error: --repo must be in OWNER/REPO format", file=sys.stderr)
            sys.exit(1)
        return parts[0], parts[1], int(pr_arg)

    print(f"Error: unrecognised PR reference: {pr_arg!r}", file=sys.stderr)
    sys.exit(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve unresolved Copilot review threads on a GitHub PR."
    )
    parser.add_argument("pr", help="PR URL or PR number")
    parser.add_argument("--repo", help="OWNER/REPO (required when pr is a number)")
    parser.add_argument(
        "--sha",
        help="Commit SHA to reference in resolution comments (defaults to PR head SHA)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List unresolved threads without resolving them",
    )
    args = parser.parse_args(argv)

    owner, repo, pr_number = parse_pr_ref(args.pr, args.repo)

    print(
        f"Scanning PR #{pr_number} in {owner}/{repo} for unresolved Copilot threads..."
    )

    head_sha, threads = load_threads(owner, repo, pr_number)
    sha = args.sha if args.sha else head_sha[:12]

    copilot_unresolved = [
        t
        for t in threads
        if not t["isOutdated"]
        and not t["isResolved"]
        and is_copilot_login(t["starterLogin"])
    ]

    print(f"  Total threads: {len(threads)}")
    print(f"  Unresolved Copilot threads: {len(copilot_unresolved)}")

    if not copilot_unresolved:
        print("Nothing to resolve.")
        return 0

    if args.dry_run:
        print("\n[dry-run] Would resolve:")
        for i, t in enumerate(copilot_unresolved, 1):
            print(f"  {i}. {t['id']}  (started by {t['starterLogin']})")
        return 0

    resolved_count = 0
    for i, t in enumerate(copilot_unresolved, 1):
        tid = t["id"]
        label = f"[{i}/{len(copilot_unresolved)}]"
        comment_body = f"Addressed in commit {sha}."
        print(f"  {label} Replying on thread {tid}...")
        post_reply(tid, comment_body)
        print(f"  {label} Resolving thread {tid}...")
        ok = resolve_thread(tid)
        if ok:
            resolved_count += 1
            print("    ✓ Resolved")
        else:
            print(
                "    ✗ resolveReviewThread returned isResolved=False", file=sys.stderr
            )

    print(f"\nDone: {resolved_count}/{len(copilot_unresolved)} threads resolved.")
    return 0 if resolved_count == len(copilot_unresolved) else 1


if __name__ == "__main__":
    sys.exit(main())
