"""Tests for github_client.py with `requests` mocked."""
from types import SimpleNamespace

import pytest
import requests

from repo_diff_pipeline import github_client as gh


def _resp(status=200, json_data=None, text=''):
    return SimpleNamespace(status_code=status, json=lambda: json_data, text=text)


@pytest.mark.parametrize('url,expected', [
    ('https://github.com/octo/hello-world/pull/42', ('octo', 'hello-world', 42)),
    ('github.com/a.b/c_d/pull/1/files', ('a.b', 'c_d', 1)),
])
def test_parse_pr_url(url, expected):
    ref = gh.parse_pr_url(url)
    assert (ref.owner, ref.repo, ref.number) == expected
    assert ref.base_sha is None and ref.head_sha is None


@pytest.mark.parametrize('url', ['', 'https://gitlab.com/a/b/pull/1', 'https://github.com/a/b/issues/1'])
def test_parse_pr_url_rejects_non_pr(url):
    with pytest.raises(gh.GitHubError):
        gh.parse_pr_url(url)


def test_not_connected_error_is_a_github_error():
    assert issubclass(gh.GitHubNotConnectedError, gh.GitHubError)


def test_check_connected_without_token_makes_no_request(monkeypatch):
    monkeypatch.setattr(gh.requests, 'get', lambda *a, **k: pytest.fail('should not call the API'))
    assert gh.check_github_connected(None) is False
    assert gh.check_github_connected('') is False


def test_check_connected_true_on_200(monkeypatch):
    seen = {}
    monkeypatch.setattr(gh.requests, 'get', lambda url, **kw: seen.update(url=url, **kw) or _resp(200))
    assert gh.check_github_connected('tok') is True
    assert seen['url'].endswith('/user')
    assert seen['headers']['Authorization'] == 'Bearer tok'
    assert seen['timeout'] == gh.REQUEST_TIMEOUT


def test_check_connected_false_on_401(monkeypatch):
    monkeypatch.setattr(gh.requests, 'get', lambda *a, **k: _resp(401))
    assert gh.check_github_connected('bad') is False


def test_check_connected_false_on_network_error(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError('down')

    monkeypatch.setattr(gh.requests, 'get', boom)
    assert gh.check_github_connected('tok') is False


def test_fetch_pr_metadata_sets_shas(monkeypatch):
    monkeypatch.setattr(gh.requests, 'get', lambda *a, **k: _resp(200, {'base': {'sha': 'b1'}, 'head': {'sha': 'h1'}}))
    ref = gh.fetch_pr_metadata(gh.PullRequestRef('o', 'r', 3), 'tok')
    assert (ref.base_sha, ref.head_sha) == ('b1', 'h1')


def test_fetch_pr_metadata_error(monkeypatch):
    monkeypatch.setattr(gh.requests, 'get', lambda *a, **k: _resp(404, text='Not Found'))
    with pytest.raises(gh.GitHubError, match='404'):
        gh.fetch_pr_metadata(gh.PullRequestRef('o', 'r', 3), 'tok')


def test_fetch_pr_diff_requests_diff_media_type(monkeypatch):
    seen = {}
    monkeypatch.setattr(gh.requests, 'get', lambda url, **kw: seen.update(url=url, **kw) or _resp(200, text='DIFF'))
    assert gh.fetch_pr_diff(gh.PullRequestRef('o', 'r', 3), 'tok') == 'DIFF'
    assert seen['headers']['Accept'] == 'application/vnd.github.v3.diff'
    assert seen['url'].endswith('/repos/o/r/pulls/3')


def test_fetch_pr_diff_error(monkeypatch):
    monkeypatch.setattr(gh.requests, 'get', lambda *a, **k: _resp(500, text='oops'))
    with pytest.raises(gh.GitHubError, match='500'):
        gh.fetch_pr_diff(gh.PullRequestRef('o', 'r', 3), 'tok')


def test_clone_url_for_pr():
    assert gh.clone_url_for_pr(gh.PullRequestRef('o', 'r', 3)) == 'https://github.com/o/r.git'
