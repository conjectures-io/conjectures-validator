from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from verifier.errors import VerifierError
from verifier.repository import tasks_repository_root
from verifier.task_pool import (
    HISTORICAL_OPEN_SOURCE_FAMILY,
    HISTORICAL_OPEN_TARGETS,
    _valid_source_identity,
    load_selection_audit,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def audit_copy(tmp_path):
    source = tasks_repository_root(ROOT)
    for path in ('tiers', 'staged'):
        shutil.copytree(source / path, tmp_path / path)
    return tmp_path / 'tiers/tier-1/selection-audit.json'


def test_historical_admission_requires_exact_declaration():
    family = HISTORICAL_OPEN_SOURCE_FAMILY
    name = 'HajosCycleDecomposition'
    path = f'FormalConjectures/HistoricalOpen/{name}.lean'
    theorem = f'FullOldProblemsCatalog.{name}.conjecture'
    assert len(HISTORICAL_OPEN_TARGETS) == 16
    assert _valid_source_identity(family, name, path, theorem)
    assert not _valid_source_identity(family, name, path, theorem + '.weaker')
    assert not _valid_source_identity(family, name, path, theorem.replace(name, 'Other'))
    assert not _valid_source_identity(family, name, path.replace(name, 'Other'), theorem)


@pytest.mark.needs_checkouts
def test_historical_selection_retains_existing_targets(audit_copy):
    audit = load_selection_audit(audit_copy)
    new = {e.theorem for e in audit.entries if e.source_family == HISTORICAL_OPEN_SOURCE_FAMILY}
    assert new == HISTORICAL_OPEN_TARGETS
    assert len(audit.entries) == 290


@pytest.mark.needs_checkouts
@pytest.mark.parametrize('change', ['digest', 'missing', 'claimed', 'variant', 'wrong-source', 'duplicate'])
def test_historical_evidence_fails_closed(audit_copy, change):
    evidence = audit_copy.with_name('historical-open-status-audit.json')
    if change == 'missing':
        evidence.unlink()
    elif change == 'digest':
        evidence.write_bytes(evidence.read_bytes() + b'\n')
    else:
        data = json.loads(evidence.read_text())
        row = data['candidates'][0]
        if change == 'claimed':
            row['full_resolution_claim_found'] = True
        elif change == 'variant':
            row['full_original_question'] = False
        elif change == 'wrong-source':
            row['source_path'] = 'FormalConjectures/HistoricalOpen/Other.lean'
        else:
            data['candidates'].append(row.copy())
        evidence.write_text(json.dumps(data))
        audit = json.loads(audit_copy.read_text())
        for source in audit['source_status_sources']:
            if source['family'] == HISTORICAL_OPEN_SOURCE_FAMILY:
                source['revision'] = hashlib.sha256(evidence.read_bytes()).hexdigest()
        audit_copy.write_text(json.dumps(audit))
    with pytest.raises(VerifierError):
        load_selection_audit(audit_copy)


def test_cantoni_claim_excludes_it_from_admission():
    assert not _valid_source_identity(
        HISTORICAL_OPEN_SOURCE_FAMILY, 'Cantoni',
        'FormalConjectures/HistoricalOpen/Cantoni.lean',
        'FullOldProblemsCatalog.Cantoni.conjecture',
    )
