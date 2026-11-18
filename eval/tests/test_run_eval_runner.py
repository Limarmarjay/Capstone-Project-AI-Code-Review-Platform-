"""Tests for the runner side of eval/run_eval.py: fixture loading, matching, the
connection-aware wrapper, and main() in single-seed and multi-seed modes.

Ollama is replaced with a scripted fake; results/baselines go to a tmp dir.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from eval import run_eval as ev
from llm_review.client import LLMTimeout


class FakeOllama:
    """Returns an empty findings list (or raises) -- no network."""
    behaviour = 'empty'
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeOllama.instances.append(self)

    def complete(self, system, user, **kw):
        if FakeOllama.behaviour == 'unreachable':
            raise LLMTimeout('Could not reach Ollama at http://x')
        if FakeOllama.behaviour == 'timeout':
            raise LLMTimeout('Ollama request timed out after 1s')
        return '{"findings": []}'


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    FakeOllama.behaviour = 'empty'
    FakeOllama.instances = []
    monkeypatch.setattr(ev, 'OllamaLLMClient', FakeOllama)
    monkeypatch.setattr(ev, 'RESULTS_DIR', str(tmp_path / 'results'))
    monkeypatch.setattr(ev, 'BASELINES_DIR', str(tmp_path / 'baselines'))
    monkeypatch.setattr(ev, 'git_commit', lambda: 'deadbeef')


def _argv(monkeypatch, *args):
    monkeypatch.setattr(sys, 'argv', ['run_eval', '--skip-setup-check', *args])


# --- fixtures ---------------------------------------------------------------------

def test_load_fixtures_returns_all_sorted():
    fixtures = ev.load_fixtures(None)
    ids = [f[0] for f in fixtures]
    assert ids == sorted(ids) and 'sql-injection-basic' in ids
    case_id, analysis_input, expected = fixtures[0]
    assert 'files' in analysis_input and 'must_catch' in expected or 'must_not_flag' in expected or expected


def test_load_fixtures_filters():
    assert [f[0] for f in ev.load_fixtures(['sql-injection-basic'])] == ['sql-injection-basic']


def test_load_fixtures_unknown_exits():
    with pytest.raises(SystemExit, match='Unknown fixture'):
        ev.load_fixtures(['nope'])


# --- extract / match --------------------------------------------------------------------

def test_extract_model_findings_drops_pure_static():
    out = ev.extract_model_findings({'findings': [
        {'source': 'static', 'line': 1},
        {'source': 'llm', 'line': 2, 'static_finding_ids': []},
        {'source': 'combined', 'line': 3, 'static_finding_ids': ['sf1', 'sf2']},
    ]})
    assert [(f['line'], f['relates_to_static_finding_id']) for f in out] == [(2, None), (3, 'sf1')]


def _mf(line, category='security', link=None, title='t'):
    return {'line': line, 'category': category, 'relates_to_static_finding_id': link, 'title': title}


def _exp(id_, line, category='security', link=None):
    return {'id': id_, 'line': line, 'category': category, 'relates_to_static_finding_id': link}


def test_match_must_catch_within_one_line_and_link_correctness():
    expected = {'must_catch': [_exp('a', 10, link='sf1')]}
    result = ev.match_fixture([_mf(11, link='sf1')], expected)
    assert result['matched_must_catch'] == [{'id': 'a', 'line': 11, 'category': 'security', 'link_correct': True}]
    assert result['missed_must_catch'] == [] and result['unexpected'] == []

    result = ev.match_fixture([_mf(10, link=None)], expected)
    assert result['matched_must_catch'][0]['link_correct'] is False


def test_match_requires_category_and_line_tolerance():
    expected = {'must_catch': [_exp('a', 10)]}
    result = ev.match_fixture([_mf(12), _mf(10, category='bug')], expected)
    assert result['missed_must_catch'] == ['a']
    assert len(result['unexpected']) == 2


def test_match_may_catch_and_must_not_flag_and_consumption():
    expected = {
        'must_catch': [], 'may_catch': [_exp('m', 5)], 'must_not_flag': [{'line': 20}],
    }
    result = ev.match_fixture([_mf(5), _mf(5), _mf(20, category='anything')], expected)
    assert [m['id'] for m in result['matched_may_catch']] == ['m']
    assert len(result['unexpected']) == 1            # second hit on line 5: expectation already consumed
    assert len(result['must_not_flag_hits']) == 1
    assert result['may_catch_total'] == 1 and result['missed_may_catch'] == []


def test_match_handles_missing_expectation_keys():
    result = ev.match_fixture([_mf(1)], {})
    assert result['must_catch_total'] == 0 and len(result['unexpected']) == 1


# --- wrapper ----------------------------------------------------------------------------

def test_wrapper_flags_connection_failures_but_not_plain_timeouts():
    wrapper = ev._ConnectionAwareOllamaClient(FakeOllama())
    FakeOllama.behaviour = 'timeout'
    with pytest.raises(LLMTimeout):
        wrapper.complete('s', 'u')
    assert not wrapper.connection_failure_seen

    FakeOllama.behaviour = 'unreachable'
    with pytest.raises(LLMTimeout):
        wrapper.complete('s', 'u')
    assert wrapper.connection_failure_seen and 'Could not reach' in wrapper.connection_failure_detail


def test_wrapper_passes_through_success():
    assert ev._ConnectionAwareOllamaClient(FakeOllama()).complete('s', 'u') == '{"findings": []}'


# --- run_one_fixture ------------------------------------------------------------------------

def _factory():
    return ev._ConnectionAwareOllamaClient(FakeOllama())


def test_run_one_fixture_scores_a_clean_run():
    case_id, analysis_input, expected = ev.load_fixtures(['python-clean-add-logging'])[0]
    result, failed = ev.run_one_fixture(case_id, analysis_input, expected, _factory)
    assert failed is False
    assert result['case_id'] == case_id and result['latency_seconds'] >= 0
    assert result['must_catch_total'] == len(expected.get('must_catch', []))


def test_run_one_fixture_reports_connection_failure():
    FakeOllama.behaviour = 'unreachable'
    case_id, analysis_input, expected = ev.load_fixtures(['python-clean-add-logging'])[0]
    result, failed = ev.run_one_fixture(case_id, analysis_input, expected, _factory)
    assert failed is True and 'Could not reach' in result['connection_failure']


# --- small helpers -----------------------------------------------------------------------------

def test_print_pass_rate_table(capsys):
    ev.print_pass_rate_table({'a': {'passes': 1, 'total': 2, 'rate': 0.5}, 'b': {'passes': 0, 'total': 0, 'rate': None}}, ['a'])
    out = capsys.readouterr().out
    assert '1/2  (50%)  <- UNSTABLE' in out and 'n/a' in out
    ev.print_pass_rate_table({}, [])  # empty must not crash


def test_git_commit_real_and_failure(monkeypatch):
    monkeypatch.undo()  # drop the autouse stub so the real function runs
    assert len(ev.git_commit()) in (40, 7) or ev.git_commit() == 'unknown'

    def boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(ev.subprocess, 'run', boom)
    assert ev.git_commit() == 'unknown'


def test_run_setup_check_reflects_exit_code(monkeypatch):
    seen = {}
    monkeypatch.setattr(ev.subprocess, 'run', lambda cmd, cwd: seen.update(cmd=cmd) or subprocess.CompletedProcess(cmd, 0))
    assert ev.run_setup_check('m', 'http://h', 5.0) is True
    assert seen['cmd'][-6:] == ['--model', 'm', '--host', 'http://h', '--timeout', '5.0'] or '--model' in seen['cmd']
    monkeypatch.setattr(ev.subprocess, 'run', lambda cmd, cwd: subprocess.CompletedProcess(cmd, 1))
    assert ev.run_setup_check('m', 'h', 1.0) is False


# --- main ---------------------------------------------------------------------------------------

def test_main_single_run_writes_results_and_baseline(monkeypatch, tmp_path, capsys):
    _argv(monkeypatch, '--fixture', 'python-clean-add-logging', '--save-baseline', 'unit', '--seed', '7', '--temperature', '0.1')
    assert ev.main() == 0
    (result_file,) = list((tmp_path / 'results').iterdir())
    report = json.loads(result_file.read_text())
    assert report['metadata']['seed'] == 7 and report['metadata']['git_commit'] == 'deadbeef'
    assert len(report['metadata']['system_prompt_sha256']) == 64
    assert report['fixtures'][0]['case_id'] == 'python-clean-add-logging'
    assert json.loads((tmp_path / 'baselines' / 'unit.json').read_text()) == report
    assert FakeOllama.instances[0].kwargs['seed'] == 7
    assert '=== Summary ===' in capsys.readouterr().out


def test_main_single_run_aborts_when_ollama_drops(monkeypatch, tmp_path, capsys):
    FakeOllama.behaviour = 'unreachable'
    _argv(monkeypatch, '--fixture', 'python-clean-add-logging')
    assert ev.main() == 1
    assert 'ABORT' in capsys.readouterr().out
    assert not (tmp_path / 'results').exists()  # nothing saved on connection failure


def test_main_multi_seed(monkeypatch, tmp_path, capsys):
    _argv(monkeypatch, '--fixture', 'python-clean-add-logging', '--seeds', '1,2', '--save-baseline', 'multi')
    assert ev.main() == 0
    report = json.loads((tmp_path / 'baselines' / 'multi.json').read_text())
    assert [s['seed'] for s in report['per_seed']] == [1, 2]
    assert 'stability' in report and report['metadata']['seeds'] == [1, 2]
    out = capsys.readouterr().out
    assert 'Per-fixture pass rate' in out and 'No unstable fixtures' in out
    assert sorted(i.kwargs['seed'] for i in FakeOllama.instances) == [1, 2]


def test_main_multi_seed_aborts_on_connection_failure(monkeypatch, tmp_path, capsys):
    FakeOllama.behaviour = 'unreachable'
    _argv(monkeypatch, '--fixture', 'python-clean-add-logging', '--seeds', '1,2')
    assert ev.main() == 1
    assert 'ABORT' in capsys.readouterr().out and not (tmp_path / 'results').exists()


def test_main_bad_seeds_exit(monkeypatch):
    _argv(monkeypatch, '--seeds', 'x,y')
    with pytest.raises(SystemExit):
        ev.main()


def test_main_runs_setup_check_and_aborts_when_it_fails(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['run_eval'])
    monkeypatch.setattr(ev, 'run_setup_check', lambda *a: False)
    assert ev.main() == 1
    assert 'Setup check failed' in capsys.readouterr().out


def test_module_entrypoint(monkeypatch):
    import runpy

    monkeypatch.setattr(sys, 'argv', ['run_eval'])
    monkeypatch.setattr(subprocess, 'run', lambda cmd, **k: subprocess.CompletedProcess(cmd, 1))
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(ev.__file__, run_name='__main__')
    assert exc.value.code == 1


def test_main_multi_seed_lists_unstable_fixtures(monkeypatch, capsys):
    real = ev.aggregate_across_seeds

    def flaky(per_seed):
        out = real(per_seed)
        out['unstable_fixtures'] = ['python-clean-add-logging']
        return out

    monkeypatch.setattr(ev, 'aggregate_across_seeds', flaky)
    _argv(monkeypatch, '--fixture', 'python-clean-add-logging', '--seeds', '1')
    assert ev.main() == 0
    assert "Unstable fixtures" in capsys.readouterr().out
