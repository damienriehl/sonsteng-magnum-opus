"""Offline coverage of scoped drafting through map files, CLI and RPC encoding."""
import io
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import editor_scoped_drafts as sd
from test_editor_scoped_drafts import fixture_map


def request(**patch):
    row = dict(id='r1', level='course', instruction='Revise wording',
               status='requested', phase='canary', group_id=None,
               canary_matter=None)
    row.update(patch)
    return row


class RpcBoundary:
    """Only the external HTTP service is substituted; client encoding is real."""
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.proposals = {}
        self.outcomes = {}
        self.claim_ok = True
        self.propose_ok = True

    def __call__(self, req, timeout):
        url = urlsplit(req.full_url)
        route = url.path
        body = json.loads(req.data) if req.data else None
        self.calls.append((req.method, route, body))
        assert req.get_header('Authorization') == 'Bearer fixture-token'
        assert req.get_header('X-edit-request') == '1'
        if route.endswith('/scoped-requests'):
            status = parse_qs(url.query)['status'][0]
            result = {'items': [dict(r) for r in self.rows if r['status'] == status]}
        elif route.endswith('/scoped-claim'):
            result = {'ok': self.claim_ok}
            if self.claim_ok:
                next(r for r in self.rows if r['id'] == body['id'])['status'] = 'drafting'
        elif route.endswith('/scoped-resolve'):
            next(r for r in self.rows if r['id'] == body['id']).update(body)
            result = {'ok': True}
        elif route.endswith('/system-suggest'):
            if self.propose_ok:
                self.proposals[body['id']] = body
            result = {'ok': self.propose_ok}
        elif route.endswith('/group-status'):
            result = {'outcome': self.outcomes.get(parse_qs(url.query)['group_id'][0], {})}
        else:
            raise AssertionError(route)
        return io.BytesIO(json.dumps(result).encode())


def configure(monkeypatch, tmp_path, rows):
    path = tmp_path / 'editor-map.json'
    path.write_text(json.dumps(fixture_map()), encoding='utf-8')
    monkeypatch.setattr(sd, 'MAP_PATH', str(path))
    monkeypatch.setenv(sd.ap.ENV_API_BASE, 'https://fixture.invalid/edit/v1')
    monkeypatch.setenv(sd.ap.ENV_SERVICE_TOKEN, 'fixture-token')
    rpc = RpcBoundary(rows)
    monkeypatch.setattr(sd.ap.urllib.request, 'urlopen', rpc)
    return rpc


def revise(prompt):
    blocks = json.loads(prompt.split('BLOCKS_JSON:', 1)[1])['blocks']
    return json.dumps({'result': '```json\n' + json.dumps({'drafts': [
        {'source_ref': b['source_ref'], 'new_text': b['original_text'] + ' Revised.'}
        for b in blocks]}) + '\n```'})


def test_main_real_map_client_canary_remainder_and_repeated_passes(monkeypatch, tmp_path):
    row = request()
    rpc = configure(monkeypatch, tmp_path, [row])
    monkeypatch.setattr(sd, 'run_cli', revise)
    assert sd.main([]) == 0
    assert row['status'] == 'drafted'
    assert row['canary_matter'] == 'm01-alpha'
    assert len(rpc.proposals) == 3
    scalar = next(p for p in rpc.proposals.values() if p['source_ref'].endswith('#caption'))
    assert scalar['json_path'] == 'caption'
    assert scalar['origin'] == 'ai_rewrite'
    assert scalar['comment'] == 'Scoped change (course): Revise wording'
    initial = dict(rpc.proposals)
    assert sd.main([]) == 0
    assert rpc.proposals == initial
    rpc.outcomes[row['group_id']] = {'total': 3, 'by_status': {'applied': 3}}
    assert sd.main([]) == 0
    assert row['phase'] == 'remainder'
    assert row['group_id'] == 'scoped-r1-remainder'
    assert len(rpc.proposals) == 6
    assert set(initial).issubset(rpc.proposals)
    assert sd.main([]) == 0
    assert len(rpc.proposals) == 6
    rpc.outcomes[row['group_id']] = {'total': 3, 'by_status': {'applied': 3}}
    assert sd.main([]) == 0
    assert row['status'] == 'done'
    before = len([c for c in rpc.calls if c[0] == 'POST'])
    assert sd.main([]) == 0
    assert len([c for c in rpc.calls if c[0] == 'POST']) == before


def test_dry_run_enumerates_each_status_without_mutations(monkeypatch, tmp_path, capsys):
    rows = [request(id=str(i), status=status) for i, status in enumerate(('requested', 'drafting', 'drafted'))]
    rpc = configure(monkeypatch, tmp_path, rows)
    assert sd.main(['--dry-run']) == 0
    output = capsys.readouterr().out
    for status in ('requested', 'drafting', 'drafted'):
        assert status + ': 1' in output
    assert output.count('blocks=3 canary=m01-alpha') == 3
    assert all(method == 'GET' for method, _, _ in rpc.calls)


@pytest.mark.parametrize('missing', ['token', 'base', 'map'])
def test_main_configuration_errors(monkeypatch, tmp_path, capsys, missing):
    rpc = configure(monkeypatch, tmp_path, [])
    if missing == 'token':
        monkeypatch.delenv(sd.ap.ENV_SERVICE_TOKEN)
    elif missing == 'base':
        monkeypatch.delenv(sd.ap.ENV_API_BASE)
    else:
        monkeypatch.setattr(sd, 'MAP_PATH', str(tmp_path / 'absent.json'))
    assert sd.main([]) == 2
    assert 'error:' in capsys.readouterr().err
    assert not rpc.calls


@pytest.mark.parametrize('params', [dict(level='part', matter='m01-alpha', part='absent'),
    dict(level='part', matter='absent', part='matter'), dict(level='module', module='absent'),
    dict(level='invalid'), dict(level='matter')])
def test_unknown_scopes_fail_closed(params):
    with pytest.raises(sd.ScopedError, match='unknown'):
        sd.enumerate_blocks(fixture_map(), params)


def test_scalar_part_and_empty_scope_membership():
    blocks = sd.enumerate_blocks(fixture_map(), dict(level='part', matter='m01-alpha', part='matter'))
    assert [b['source_ref'] for b in blocks] == ['data/matters/m01-alpha/matter.json#caption']
    assert sd.enumerate_blocks({}, {'level': 'course'}) == []
    assert sd.module_members({}, {'level': 'course'}) == []
    assert sd.module_members({}, {'level': 'matter'}) == []
    assert sd.pick_canary([]) is None
    assert sd._blocks_for_phase({}, request()) == ([], None)


@pytest.mark.parametrize('raw, expected', [
    ('not JSON', []), ('prefix {broken} suffix', []), ('{"drafts": null}', []),
    ('{"result":"no relevant blocks"}', []), ('{"result":"{}"}', []),
    ('prefix {"drafts": [{"source_ref": "a", "new_text": "b"}]} suffix', [{'source_ref': 'a', 'new_text': 'b'}]),
])
def test_parser_envelopes_and_unusable_output(raw, expected):
    assert sd.parse_drafts(raw) == expected


def test_chunk_boundaries_reject_cross_chunk_refs_and_duplicate_drafts():
    blocks = [{'source_ref': 'ref-%d' % i, 'original_text': 'old'} for i in range(61)]
    sizes = []
    def cli(prompt):
        chunk = json.loads(prompt.split('BLOCKS_JSON:', 1)[1])['blocks']
        sizes.append(len(chunk))
        drafts = [{'source_ref': b['source_ref'], 'new_text': ' new '} for b in chunk]
        drafts += [{'source_ref': chunk[0]['source_ref'], 'new_text': 'second'},
                   {'source_ref': 'unknown', 'new_text': 'bad'},
                   {'source_ref': 'ref-60', 'new_text': 'new'}]
        return json.dumps({'drafts': drafts})
    result = sd.draft_blocks('Revise', blocks, cli)
    assert sizes == [30, 30, 1]
    assert len(result) == 61
    assert all(d['new_text'] == 'new' for d in result)
    assert sd.draft_blocks('Revise', [], cli) == []
    assert sizes == [30, 30, 1]


def test_invalid_drafts_do_not_reserve_reference_before_valid_draft():
    block = {'source_ref': 'r', 'original_text': ' original '}
    drafts = [{'source_ref': 'r', 'new_text': text} for text in
              (None, 123, '', '   ', 'original', '{#b:reserved}', ' valid ', 'duplicate')]
    assert sd.validate_drafts(drafts, [block]) == [{'source_ref': 'r', 'new_text': 'valid', 'block': block}]
    assert sd.validate_drafts(None, [block]) == []


@pytest.mark.parametrize('mode', ['claim-lost', 'invalid-scope', 'cli-error', 'empty', 'declined', 'empty-remainder'])
def test_orchestration_alternate_paths_with_real_http_client(monkeypatch, tmp_path, mode):
    row = request()
    if mode in ('declined', 'empty-remainder'):
        row.update(status='drafted', group_id='scoped-r1-canary', canary_matter='m01-alpha')
    if mode == 'invalid-scope':
        row.update(level='module', module='absent')
    rpc = configure(monkeypatch, tmp_path, [row])
    rpc.claim_ok = mode != 'claim-lost'
    if mode in ('declined', 'empty-remainder'):
        rpc.outcomes[row['group_id']] = {'total': 1, 'by_status': {('declined' if mode == 'declined' else 'applied'): 1}}
    def cli(prompt):
        if mode == 'cli-error':
            raise sd.ScopedError('fixture CLI unavailable')
        return '{"drafts": []}'
    monkeypatch.setattr(sd, 'run_cli', cli)
    assert sd.main([]) == 0
    expected = {'claim-lost': 'requested', 'declined': 'declined', 'empty-remainder': 'done'}
    assert row['status'] == expected.get(mode, 'failed')
    assert rpc.proposals == {}
    if mode == 'empty-remainder':
        assert row['phase'] == 'remainder'
        assert row['note'] == 'canary applied; remainder empty'


def executable(tmp_path, body):
    path = tmp_path / 'model-fixture'
    path.write_text('#!' + sys.executable + '\n' + body, encoding='utf-8')
    path.chmod(0o755)
    return str(path)


def test_run_cli_real_subprocess_arguments_and_unicode(tmp_path):
    cli = executable(tmp_path, 'import json,sys\nprint(json.dumps(sys.argv[1:]))\n')
    assert json.loads(sd.run_cli('Unicode café; $(no shell)', cli=cli, model='fixture')) == [
        '-p', 'Unicode café; $(no shell)', '--model', 'fixture', '--output-format', 'json']


@pytest.mark.parametrize('failure', ['missing', 'exit', 'timeout'])
def test_run_cli_real_process_failures(tmp_path, failure):
    if failure == 'missing':
        cli = str(tmp_path / 'nonexistent')
    else:
        body = 'import sys\nsys.stderr.write("x" * 450 + "TAIL")\nsys.exit(7)\n' if failure == 'exit' else 'import time\ntime.sleep(5)\n'
        cli = executable(tmp_path, body)
    with pytest.raises(sd.ScopedError, match='cli exit 7' if failure == 'exit' else 'cli unavailable') as err:
        sd.run_cli('p', cli=cli, timeout=0.05 if failure == 'timeout' else 2)
    if failure == 'exit':
        assert str(err.value).endswith('x' * 396 + 'TAIL')
        assert len(str(err.value)) == len('cli exit 7: ') + 400


def test_submission_counts_only_success_and_uses_stable_ids(monkeypatch, tmp_path):
    rpc = configure(monkeypatch, tmp_path, [])
    client = sd.ScopedHttpClient('https://fixture.invalid/edit/v1', 'fixture-token')
    drafts = sd.draft_blocks('Revise', sd.enumerate_blocks(fixture_map(), {'level': 'course'}), revise)
    assert sd.submit_drafts(client, request(), drafts, 'g1') == 6
    original_ids = set(rpc.proposals)
    assert sd.submit_drafts(client, request(), drafts, 'g2') == 6
    assert set(rpc.proposals) == original_ids
    assert sd._draft_id('another-request', drafts[0]['source_ref']) not in original_ids
    rpc.propose_ok = False
    assert sd.submit_drafts(client, request(), drafts, 'g3') == 0
