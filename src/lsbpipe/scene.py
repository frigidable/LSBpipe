"""Versioned scene masks and operation-specific validity decisions.

LSBpipe v0.3 P0a. No mask filling, source subtraction or sky estimation.
Pixel masks are Boolean arrays: True denotes membership in a named layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping
import base64
import hashlib
import json
import re

import numpy as np


SCHEMA_VERSION = '0.3.0'


class LayerKind(str, Enum):
    INVALID_DATA = 'invalid_data'
    NO_COVERAGE = 'no_coverage'
    SATURATION = 'saturation'
    INSTRUMENTAL_DEFECT = 'instrumental_defect'
    STAR_CORE = 'star_core'
    STAR_WINGS = 'star_wings'
    NEIGHBOR = 'neighbor'
    TARGET_PROTECT = 'target_protect'
    MANUAL_EXCLUDE = 'manual_exclude'
    SKY_TRAIN = 'sky_train'
    SKY_HOLDOUT = 'sky_holdout'
    CATALOG_WARNING = 'catalog_warning'


class Operation(str, Enum):
    PHOTOMETRY = 'photometry'
    GEOMETRY = 'geometry'
    SKY_FIT = 'sky_fit'
    SKY_HOLDOUT = 'sky_holdout'


_HARD = frozenset({LayerKind.INVALID_DATA, LayerKind.NO_COVERAGE,
                   LayerKind.SATURATION, LayerKind.INSTRUMENTAL_DEFECT})
_CONTAMINATION = frozenset({LayerKind.STAR_CORE, LayerKind.STAR_WINGS,
                            LayerKind.NEIGHBOR, LayerKind.MANUAL_EXCLUDE})
_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')


def _immutable_bool(value: np.ndarray) -> np.ndarray:
    raw = np.asarray(value)
    if raw.ndim != 2 or raw.dtype.kind != 'b' or raw.size == 0:
        raise ValueError('Mask layer must be a nonempty 2D Boolean array')
    # Immutable bytes own the storage, so callers cannot flip writeability back on.
    return np.frombuffer(raw.tobytes(order='C'), dtype=np.bool_).reshape(raw.shape)


def grid_fingerprint(wcs: object, shape: tuple[int, int]) -> str:
    """Strict identity of a WCS header and 2D shape; not reprojection equivalence.

    This intentionally rejects distinct header representations, even if their
    celestial transforms are approximately equivalent. Co-registration checks
    for imported FITS remain the responsibility of ObservationSet ingestion.
    """
    if len(shape) != 2 or any(not isinstance(n, (int, np.integer)) or n <= 0 for n in shape):
        raise ValueError('shape must be two positive integers')
    try:
        header = wcs.to_header(relax=True)
        cards = sorted((str(k), str(v)) for k, v in header.items())
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError('Grid fingerprint requires a serializable WCS header') from exc
    payload = json.dumps({'shape': list(map(int, shape)), 'wcs': cards},
                         sort_keys=True, separators=(',', ':'))
    return 'wcs-sha256:' + hashlib.sha256(payload.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class MaskLayer:
    name: str
    kind: LayerKind
    data: np.ndarray
    grid_id: str
    band: str | None = None
    source: str = 'manual'

    def __post_init__(self):
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise ValueError('Layer name must be a safe nonempty identifier')
        object.__setattr__(self, 'kind', LayerKind(self.kind))
        if not isinstance(self.grid_id, str) or not self.grid_id:
            raise ValueError('Layer needs a nonempty grid_id')
        if self.band is not None and self.band not in ('g', 'r', 'i', 'z'):
            raise ValueError('Layer band must be g/r/i/z or None')
        if not isinstance(self.source, str):
            raise TypeError('source must be a string')
        object.__setattr__(self, 'data', _immutable_bool(self.data))

    def to_dict(self) -> dict:
        packed = np.packbits(self.data.ravel(order='C'))
        return {'name': self.name, 'kind': self.kind.value,
                'shape': list(self.data.shape), 'grid_id': self.grid_id,
                'band': self.band, 'source': self.source,
                'bits_base64': base64.b64encode(packed.tobytes()).decode('ascii')}

    @classmethod
    def from_dict(cls, record: Mapping) -> 'MaskLayer':
        shape = tuple(record['shape'])
        if len(shape) != 2 or any(not isinstance(n, int) or n <= 0 for n in shape):
            raise ValueError('Invalid encoded layer shape')
        raw = base64.b64decode(record['bits_base64'], validate=True)
        needed = (shape[0] * shape[1] + 7) // 8
        if len(raw) != needed:
            raise ValueError('Packed mask length mismatch')
        values = np.unpackbits(np.frombuffer(raw, dtype=np.uint8))[:shape[0] * shape[1]]
        return cls(name=record['name'], kind=LayerKind(record['kind']),
                   data=values.reshape(shape).astype(bool), grid_id=record['grid_id'],
                   band=record.get('band'), source=record.get('source', 'manual'))


@dataclass(frozen=True)
class SceneState:
    target_id: str
    grid_id: str
    band: str
    shape: tuple[int, int]
    layers: tuple[MaskLayer, ...] = ()
    revision: int = 0

    def __post_init__(self):
        if not isinstance(self.target_id, str) or not self.target_id.strip():
            raise ValueError('target_id required')
        if not isinstance(self.grid_id, str) or not self.grid_id:
            raise ValueError('grid_id required')
        if self.band not in ('g', 'r', 'i', 'z'):
            raise ValueError('Unsupported band')
        if len(self.shape) != 2 or any(not isinstance(s, (int, np.integer)) or s <= 0 for s in self.shape):
            raise ValueError('shape must contain two positive integers')
        if not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError('revision must be non-negative integer')
        object.__setattr__(self, 'shape', tuple(map(int, self.shape)))
        object.__setattr__(self, 'layers', tuple(self.layers))
        names = set()
        train = np.zeros(self.shape, dtype=bool)
        holdout = np.zeros(self.shape, dtype=bool)
        for layer in self.layers:
            if not isinstance(layer, MaskLayer):
                raise TypeError('layers must contain MaskLayer')
            if layer.name in names:
                raise ValueError(f'Duplicate layer: {layer.name}')
            names.add(layer.name)
            if layer.grid_id != self.grid_id or layer.data.shape != self.shape:
                raise ValueError(f'Grid/shape mismatch for {layer.name}')
            if layer.band is not None and layer.band != self.band:
                raise ValueError(f'Band mismatch for {layer.name}')
            if layer.kind == LayerKind.SKY_TRAIN:
                train |= layer.data
            elif layer.kind == LayerKind.SKY_HOLDOUT:
                holdout |= layer.data
        if np.any(train & holdout):
            raise ValueError('Sky training and holdout regions overlap')

    def with_layer(self, layer: MaskLayer) -> 'SceneState':
        if any(existing.name == layer.name for existing in self.layers):
            raise ValueError('Layer already exists; remove explicitly before replacement')
        return SceneState(self.target_id, self.grid_id, self.band, self.shape,
                          self.layers + (layer,), self.revision + 1)

    def without_layer(self, name: str) -> 'SceneState':
        updated = tuple(layer for layer in self.layers if layer.name != name)
        if len(updated) == len(self.layers):
            raise KeyError(name)
        return SceneState(self.target_id, self.grid_id, self.band, self.shape,
                          updated, self.revision + 1)

    def to_dict(self) -> dict:
        return {'schema_version': SCHEMA_VERSION, 'target_id': self.target_id,
                'grid_id': self.grid_id, 'band': self.band,
                'shape': list(self.shape), 'revision': self.revision,
                'layers': [layer.to_dict() for layer in self.layers]}

    @classmethod
    def from_dict(cls, record: Mapping) -> 'SceneState':
        if record.get('schema_version') != SCHEMA_VERSION:
            raise ValueError('Unsupported scene schema version')
        return cls(target_id=record['target_id'], grid_id=record['grid_id'],
                   band=record['band'], shape=tuple(record['shape']),
                   layers=tuple(MaskLayer.from_dict(v) for v in record['layers']),
                   revision=record['revision'])


@dataclass(frozen=True)
class MaskDecision:
    operation: Operation
    valid: np.ndarray
    excluded_by: Mapping[str, np.ndarray]

    def __post_init__(self):
        object.__setattr__(self, 'valid', _immutable_bool(self.valid))
        frozen = {name: _immutable_bool(values) for name, values in self.excluded_by.items()}
        object.__setattr__(self, 'excluded_by', MappingProxyType(frozen))

    @property
    def n_valid(self) -> int:
        return int(np.count_nonzero(self.valid))


def evaluate_mask(scene: SceneState, image: np.ndarray, inverse_variance: np.ndarray,
                  *, grid_id: str, operation: Operation | str,
                  observation_valid: np.ndarray | None = None) -> MaskDecision:
    """Return operation-specific valid pixels without mutating observations.

    SKY_FIT and SKY_HOLDOUT require explicitly annotated, disjoint regions.
    All operations reject hard defects and foreground contamination. Target
    protection only excludes sky samples; it never restores masked pixels.
    """
    if grid_id != scene.grid_id:
        raise ValueError('WCS/grid_id mismatch even when shape matches')
    op = Operation(operation)
    image = np.asarray(image)
    ivar = np.asarray(inverse_variance)
    if image.shape != scene.shape or ivar.shape != scene.shape:
        raise ValueError('image and inverse variance must match scene shape')
    excluded = {'NONFINITE_IMAGE': ~np.isfinite(image),
                'BAD_INVERSE_VARIANCE': ~np.isfinite(ivar) | (ivar <= 0)}
    if observation_valid is not None:
        valid0 = np.asarray(observation_valid)
        if valid0.shape != scene.shape or valid0.dtype.kind != 'b':
            raise ValueError('observation_valid must be a same-grid Boolean array')
        excluded['OBSERVATION_REJECTED'] = ~valid0
    for layer in scene.layers:
        if layer.kind in _HARD or layer.kind in _CONTAMINATION or (
            op in (Operation.SKY_FIT, Operation.SKY_HOLDOUT) and
            layer.kind == LayerKind.TARGET_PROTECT
        ):
            excluded[layer.name] = layer.data
    if op in (Operation.SKY_FIT, Operation.SKY_HOLDOUT):
        selected_kind = LayerKind.SKY_TRAIN if op == Operation.SKY_FIT else LayerKind.SKY_HOLDOUT
        selected = np.zeros(scene.shape, dtype=bool)
        for layer in scene.layers:
            if layer.kind == selected_kind:
                selected |= layer.data
        if not np.any(selected):
            raise ValueError(f'Explicit {selected_kind.value} region is required')
        excluded['NOT_IN_SELECTED_SKY_REGION'] = ~selected
    rejected = np.zeros(scene.shape, dtype=bool)
    for values in excluded.values():
        rejected |= values
    return MaskDecision(op, ~rejected, excluded)


def scene_from_observation(observation, target_id: str) -> SceneState:
    """Construct conservative default layers from v0.2 ObservationSet metadata.

    SATUR_* remains a conservative exclusion here even if v0.2 ingestion used
    reject_satur=False; use separate diagnostic runs to assess such pixels.
    """
    grid = grid_fingerprint(observation.wcs, observation.shape)
    shape = observation.shape
    items = []
    def add(name, kind, data):
        if np.any(data):
            items.append(MaskLayer(name, kind, np.asarray(data, dtype=bool), grid,
                                   band=observation.band, source='Legacy observation'))
    data = observation.image_nMgy
    ivar = observation.inverse_variance_nMgy2
    add('invalid_values', LayerKind.INVALID_DATA,
        ~np.isfinite(data) | ~np.isfinite(ivar) | (ivar <= 0))
    reasons = observation.mask_reasons
    band = observation.band.upper()
    add('native_saturation', LayerKind.SATURATION,
        reasons.get('SATUR_' + band, np.zeros(shape, dtype=bool)))
    add('native_allmask', LayerKind.INSTRUMENTAL_DEFECT,
        reasons.get('ALLMASK_' + band, np.zeros(shape, dtype=bool)))
    add('observation_rejected', LayerKind.INSTRUMENTAL_DEFECT, ~observation.valid)
    for name in ('BRIGHT', 'MEDIUM', 'GALAXY', 'NPRIMARY', 'BAILOUT'):
        add('native_warning_' + name.lower(), LayerKind.CATALOG_WARNING,
            reasons.get(name, np.zeros(shape, dtype=bool)))
    return SceneState(target_id, grid, observation.band, shape, tuple(items))
