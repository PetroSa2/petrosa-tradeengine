"""Tests for scripts/resolve-copilot-comments.py"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Load the script as a module (it lives outside the package tree)
# ---------------------------------------------------------------------------
_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "resolve-copilot-comments.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "resolve_copilot_comments", _SCRIPT_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rcc = _load_module()


# ---------------------------------------------------------------------------
# is_copilot_login
# ---------------------------------------------------------------------------


class TestIsCopilotLogin:
    def test_copilot_lowercase(self):
        assert rcc.is_copilot_login("copilot") is True

    def test_copilot_pr_reviewer(self):
        assert rcc.is_copilot_login("copilot-pull-request-reviewer") is True

    def test_copilot_pr_reviewer_bracket_suffix(self):
        assert rcc.is_copilot_login("copilot-pull-request-reviewer[bot]") is True

    def test_case_insensitive(self):
        assert rcc.is_copilot_login("Copilot") is True
        assert rcc.is_copilot_login("COPILOT-PULL-REQUEST-REVIEWER") is True

    def test_human_login(self):
        assert rcc.is_copilot_login("alice") is False

    def test_empty_string(self):
        assert rcc.is_copilot_login("") is False

    def test_none_string(self):
        assert rcc.is_copilot_login(None) is False


# ---------------------------------------------------------------------------
# parse_pr_ref
# ---------------------------------------------------------------------------


class TestParsePrRef:
    def test_full_url(self):
        owner, repo, num = rcc.parse_pr_ref(
            "https://github.com/PetroSa2/petrosa-tradeengine/pull/364", None
        )
        assert owner == "PetroSa2"
        assert repo == "petrosa-tradeengine"
        assert num == 364

    def test_number_with_repo(self):
        owner, repo, num = rcc.parse_pr_ref("42", "MyOrg/my-repo")
        assert owner == "MyOrg"
        assert repo == "my-repo"
        assert num == 42

    def test_number_without_repo_exits(self):
        with pytest.raises(SystemExit) as exc:
            rcc.parse_pr_ref("42", None)
        assert exc.value.code != 0

    def test_invalid_ref_exits(self):
        with pytest.raises(SystemExit) as exc:
            rcc.parse_pr_ref("not-a-pr", None)
        assert exc.value.code != 0

    def test_malformed_repo_exits(self):
        with pytest.raises(SystemExit) as exc:
            rcc.parse_pr_ref("42", "noslash")
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# load_threads
# ---------------------------------------------------------------------------


def _make_thread(tid, resolved, outdated, starter):
    return {
        "id": tid,
        "isResolved": resolved,
        "isOutdated": outdated,
        "comments": {"nodes": [{"author": {"login": starter}}] if starter else []},
    }


def _threads_response(threads, has_next=False, cursor=None, sha="abc123def456"):
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "headRefOid": sha,
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                        "nodes": threads,
                    },
                }
            }
        }
    }


class TestLoadThreads:
    def test_single_page(self):
        raw = [
            _make_thread("T1", False, False, "copilot"),
            _make_thread("T2", True, False, "alice"),
        ]
        response = _threads_response(raw, sha="deadbeef1234")

        with patch.object(rcc, "gh_graphql", return_value=response) as mock_gql:
            sha, threads = rcc.load_threads("Org", "repo", 1)

        assert sha == "deadbeef1234"
        assert len(threads) == 2
        assert threads[0]["id"] == "T1"
        assert threads[0]["starterLogin"] == "copilot"
        mock_gql.assert_called_once()

    def test_no_author_on_first_comment(self):
        raw = [_make_thread("T3", False, False, None)]
        with patch.object(rcc, "gh_graphql", return_value=_threads_response(raw)):
            _, threads = rcc.load_threads("Org", "repo", 1)

        assert threads[0]["starterLogin"] is None

    def test_pagination(self):
        page1 = _threads_response(
            [_make_thread("T1", False, False, "copilot")],
            has_next=True,
            cursor="CUR1",
            sha="sha111",
        )
        page2 = _threads_response(
            [_make_thread("T2", False, False, "copilot")],
            has_next=False,
            sha="sha111",
        )
        with patch.object(rcc, "gh_graphql", side_effect=[page1, page2]) as mock_gql:
            sha, threads = rcc.load_threads("Org", "repo", 1)

        assert len(threads) == 2
        assert mock_gql.call_count == 2


# ---------------------------------------------------------------------------
# main — integration-level with all I/O mocked
# ---------------------------------------------------------------------------


def _build_threads(*specs):
    """specs: list of (resolved, outdated, starter_login)"""
    return [
        {
            "id": f"TID{i}",
            "isResolved": resolved,
            "isOutdated": outdated,
            "starterLogin": starter,
        }
        for i, (resolved, outdated, starter) in enumerate(specs, 1)
    ]


class TestMain:
    def test_dry_run_does_not_resolve(self):
        threads = _build_threads(
            (False, False, "copilot"),
            (False, False, "copilot-pull-request-reviewer"),
        )
        with (
            patch.object(rcc, "load_threads", return_value=("abcdef123456", threads)),
            patch.object(rcc, "post_reply") as mock_reply,
            patch.object(rcc, "resolve_thread") as mock_resolve,
        ):
            rc = rcc.main(["https://github.com/O/R/pull/1", "--dry-run"])

        assert rc == 0
        mock_reply.assert_not_called()
        mock_resolve.assert_not_called()

    def test_nothing_to_resolve(self):
        threads = _build_threads(
            (True, False, "copilot"),
            (False, True, "copilot"),
            (False, False, "alice"),
        )
        with (
            patch.object(rcc, "load_threads", return_value=("abc", threads)),
            patch.object(rcc, "post_reply") as mock_reply,
            patch.object(rcc, "resolve_thread") as mock_resolve,
        ):
            rc = rcc.main(["https://github.com/O/R/pull/1"])

        assert rc == 0
        mock_reply.assert_not_called()
        mock_resolve.assert_not_called()

    def test_resolves_copilot_threads(self):
        threads = _build_threads(
            (False, False, "copilot"),
            (False, False, "copilot-pull-request-reviewer"),
            (False, False, "alice"),  # human — skipped
        )
        with (
            patch.object(rcc, "load_threads", return_value=("abcdef012345", threads)),
            patch.object(rcc, "post_reply") as mock_reply,
            patch.object(rcc, "resolve_thread", return_value=True) as mock_resolve,
        ):
            rc = rcc.main(["https://github.com/O/R/pull/1", "--sha", "deadbeef"])

        assert rc == 0
        assert mock_reply.call_count == 2
        assert mock_resolve.call_count == 2
        mock_reply.assert_any_call("TID1", "Addressed in commit deadbeef.")
        mock_reply.assert_any_call("TID2", "Addressed in commit deadbeef.")

    def test_uses_pr_head_sha_when_no_sha_flag(self):
        threads = _build_threads((False, False, "copilot"))
        with (
            patch.object(
                rcc, "load_threads", return_value=("fullsha9876543210", threads)
            ),
            patch.object(rcc, "post_reply") as mock_reply,
            patch.object(rcc, "resolve_thread", return_value=True),
        ):
            rcc.main(["https://github.com/O/R/pull/1"])

        # SHA is truncated to 12 chars from the head SHA
        mock_reply.assert_called_once_with("TID1", "Addressed in commit fullsha98765.")

    def test_nonzero_exit_on_partial_failure(self):
        threads = _build_threads(
            (False, False, "copilot"),
            (False, False, "copilot"),
        )
        with (
            patch.object(rcc, "load_threads", return_value=("abc", threads)),
            patch.object(rcc, "post_reply"),
            patch.object(rcc, "resolve_thread", side_effect=[True, False]),
        ):
            rc = rcc.main(["https://github.com/O/R/pull/1"])

        assert rc == 1

    def test_number_ref_with_repo_flag(self):
        threads = _build_threads((True, False, "copilot"))
        with (
            patch.object(
                rcc, "load_threads", return_value=("abc", threads)
            ) as mock_load,
            patch.object(rcc, "post_reply"),
            patch.object(rcc, "resolve_thread", return_value=True),
        ):
            rc = rcc.main(["42", "--repo", "MyOrg/my-repo"])

        assert rc == 0
        mock_load.assert_called_once_with("MyOrg", "my-repo", 42)
