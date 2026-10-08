"""Engine registration lifetime follows the host's continuation authority."""
import json
from types import SimpleNamespace

import pytest

from ouroboros import delegate_custody as custody
from ouroboros.delegate_continuation import bind_continuation
from ouroboros.owner_continue import binding_sha


class Gateway:
    def __init__(self):
        self.removed = []

    def handshake(self):
        return {}

    def remove_project(self, project_id):
        self.removed.append(project_id)

    def close(self):
        pass


def result(root, tid, **fields):
    path = root / 'task_results' / f'{tid}.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'_schema_version': 1, 'task_id': tid,
                               'status': 'completed', **fields}), encoding='utf-8')


def seed(root, *, task_id='owner', root_task_id='', source='', project='project'):
    row = custody.RunCustody(run_id='run', task_id=task_id, root_task_id=root_task_id,
                            route_id='route', model='model', project_id=project,
                            project_owned=True, source=source, ledger_root=str(root))
    assert custody.record_started(root, row, shape={'access': 'readonly', 'mode': 'ask'})
    return row


def settle(root, gateway, row):
    assert custody.settle_run(root, gateway, row, {'summary': {
        'state': 'cancelled', 'spendUsd': 0, 'spendEstimated': False}})['settled']


def sweep(root, gateway, live):
    custody.reconcile_orphaned_runs(root, live, gateway_factory=lambda: gateway)


@pytest.mark.parametrize('terminal', [
    {'status': 'completed'},
    {'status': 'cancelled', 'cancel_origin': {'source': 'owner_stop'}},
    {'status': 'failed', 'reason_code': 'budget_exhausted'},
])
def test_live_owner_can_continue_after_settlement_then_normal_finish_retires_once(tmp_path, terminal):
    gateway = Gateway()
    row = seed(tmp_path)
    result(tmp_path, 'owner', status='running')
    settle(tmp_path, gateway, row)
    assert gateway.removed == []
    sweep(tmp_path, gateway, {'owner'})
    facts, refusal, _ = bind_continuation(SimpleNamespace(task_id='owner'), tmp_path, 'run',
        actor={}, route=SimpleNamespace(route_id='route'),
        authority=SimpleNamespace(access='readonly'), target_root='')
    assert not refusal and facts['task_line'] == 'own'
    result(tmp_path, 'owner', **terminal)
    sweep(tmp_path, gateway, set())
    sweep(tmp_path, gateway, set())
    assert gateway.removed == ['project']


def test_retry_successor_keeps_predecessor_registration(tmp_path):
    gateway = Gateway()
    row = seed(tmp_path)
    result(tmp_path, 'owner', superseded_by='retry', retry_task_id='retry')
    result(tmp_path, 'retry', status='running', root_task_id='owner',
           supersedes_task_id='owner', original_task_id='owner', timeout_retry_from='owner')
    settle(tmp_path, gateway, row)
    sweep(tmp_path, gateway, {'retry'})
    assert gateway.removed == []
    result(tmp_path, 'retry', root_task_id='owner', supersedes_task_id='owner',
           original_task_id='owner', timeout_retry_from='owner')
    sweep(tmp_path, gateway, set())
    assert gateway.removed == ['project']


def test_root_offer_and_recorded_continue_chain_keep_child_run(tmp_path):
    gateway = Gateway()
    row = seed(tmp_path, task_id='child', root_task_id='root')
    result(tmp_path, 'child', parent_task_id='root', root_task_id='root')
    result(tmp_path, 'root', status='failed', reason_code='worker_crash_signal')
    settle(tmp_path, gateway, row)
    sweep(tmp_path, gateway, set())
    assert gateway.removed == []
    binding = {'predecessor_task_id': 'root', 'successor_task_id': 'next'}
    result(tmp_path, 'root', status='failed', reason_code='worker_crash_signal', continued_by={
        'successor_task_id': 'next', 'state': 'admitted', 'binding': binding,
        'binding_sha256': binding_sha(binding)})
    result(tmp_path, 'next', status='running')
    sweep(tmp_path, gateway, {'next'})
    assert gateway.removed == []
    result(tmp_path, 'next')
    sweep(tmp_path, gateway, set())
    assert gateway.removed == ['project']


@pytest.mark.parametrize('authority', ['missing', 'unreadable', 'unknown_live', 'reserved'])
def test_unknown_authority_or_reserved_owner_keeps_registration(tmp_path, authority):
    gateway = Gateway()
    row = seed(tmp_path)
    if authority != 'missing':
        result(tmp_path, 'owner')
    if authority == 'unreadable':
        (tmp_path / 'task_results/owner.json').write_text('{', encoding='utf-8')
    settle(tmp_path, gateway, row)
    custody.reconcile_orphaned_runs(tmp_path, None if authority == 'unknown_live' else set(),
        gateway_factory=lambda: gateway,
        recoverable_task_ids={'owner'} if authority == 'reserved' else set())
    assert gateway.removed == []
    assert custody.replay(tmp_path)['run'].project_owned


def test_review_settlement_keeps_immediate_retirement(tmp_path):
    gateway = Gateway()
    row = seed(tmp_path, source='review_substrate')
    settle(tmp_path, gateway, row)
    assert gateway.removed == ['project']
