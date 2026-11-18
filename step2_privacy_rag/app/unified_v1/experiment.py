"""PII-free reproducibility manifests, explicit NOT_RUN instead of fabricated stats."""
from __future__ import annotations
from dataclasses import dataclass, asdict
import importlib.metadata
import platform
import sys
from .contracts import ContractError

OWN_SHA='644ab9d56cd24d5386c739712488299448940d26'
TEAM_SHA='242543bf0eae91aa3d655227a747aa128707368e'
KOE5_SHA='237b580aea358e3042fcf5baf78124c33c3afd16'

@dataclass(frozen=True)
class ExperimentManifest:
    run_id: str
    source_sha_own: str
    source_sha_team: str
    model_artifact_sha_by_source: dict
    model_status_by_source: dict
    runtime_versions: dict
    detector_profile: str
    postprocessing_profile: str
    fusion_policy: str
    gate_policy: str
    canonicalization_version: str
    hmac_key_tag: str
    identity_policy: str
    embedding_model_sha: str
    embedding_status: str
    index_fingerprint: str
    dataset_fingerprint: str
    dataset_role: str
    run_mode: str
    timing_mode: str
    hardware: str
    peak_rss_mb: float | None
    metrics_definition_version: str
    hcx_live_status: str = 'NOT_RUN_BY_POLICY'

    def to_dict(self):
        if self.dataset_role!='synthetic' or self.run_mode!='mock':
            raise ContractError('MANIFEST_LIVE_NOT_VERIFIED')
        return asdict(self)

def mock_manifest(*,run_id,profile,key_tag,index_fingerprint,dataset_fingerprint):
    versions={'python':sys.version.split()[0]}
    for package in ['torch','transformers','tokenizers']:
        try:versions[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:versions[package]='NOT_INSTALLED'
    from .contracts import PROFILES
    return ExperimentManifest(run_id,OWN_SHA,TEAM_SHA,{},
                              {s:'MOCK_ONLY' for s in PROFILES[profile]},versions,profile,'raw_mock',
                              'evidence_v1','frozen_v6_adapter','existing_frozen_address_v22_region_v1',key_tag,
                              'anchor_conservative_v1',KOE5_SHA,'NOT_RUN_FAKE_DENSE',index_fingerprint,
                              dataset_fingerprint,'synthetic','mock','NOT_MEASURED',platform.machine(),None,'v6_common_v1')
