"""Integration: every committed eval fixture runs through the real review pipeline
(redaction -> prompt -> LLM -> combine -> severity -> score) with the mock LLM, and the
fixtures themselves are internally consistent. No Ollama/network."""
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from eval import run_eval as ev
from llm_review import review
from llm_review.client import MockLLMClient

FIXTURES = ev.load_fixtures(None)
IDS = [f[0] for f in FIXTURES]


@pytest.mark.parametrize('case_id,analysis_input,expected', FIXTURES, ids=IDS)
def test_fixture_runs_through_real_pipeline(case_id, analysis_input, expected):
    out = review(analysis_input, client=MockLLMClient())
    assert out['submission_id'] == analysis_input['submission_id']
    assert 0 <= out['overall_score'] <= 100
    assert out['llm_status'] == 'success'
    for f in out['findings']:
        assert f['severity'] in {'critical', 'high', 'medium', 'low'}
        assert f['file_path'] and isinstance(f['line'], int)

    # the grader accepts the real output shape end to end
    result = ev.match_fixture(ev.extract_model_findings(out), expected)
    assert result['must_catch_total'] == len(expected.get('must_catch', []))


@pytest.mark.parametrize('case_id,analysis_input,expected', FIXTURES, ids=IDS)
def test_fixture_expectations_are_well_formed(case_id, analysis_input, expected):
    files = {f['file_path'] for f in analysis_input['files']}
    for key in ('must_catch', 'may_catch'):
        for item in expected.get(key, []):
            assert {'id', 'category', 'line'} <= set(item), f'{case_id}:{key}'
    assert files  # every fixture has at least one file
    if case_id in ev.CLEAN_CASE_IDS:
        assert not expected.get('must_catch')  # clean cases must not demand findings


def test_clean_fixture_ids_exist():
    assert ev.CLEAN_CASE_IDS <= set(IDS)


def test_manifest_hashes_match_fixture_contents():
    manifest = json.load(open(os.path.join(ev.FIXTURES_DIR, 'MANIFEST.json')))
    for case_id, analysis_input, _ in FIXTURES:
        entry = manifest.get(case_id)
        if entry is None:
            continue
        text = ''.join(h['diff'] for f in analysis_input['files'] for h in f['hunks'])
        digest = 'sha256:' + hashlib.sha256(text.encode('utf-8')).hexdigest()
        assert digest == entry['analysis_input_hash'], f'{case_id}: fixture drifted from MANIFEST'
