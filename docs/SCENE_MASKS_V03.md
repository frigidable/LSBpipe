# LSBpipe v0.3 P0a — SceneState and MaskEngine

Status: **experimental**, no inferred scientific adoption. This increment implements scene semantics only; it does **not** fit a residual sky, subtract sources, fill masked pixels, or recover missing galaxy flux.

## Data model

- `MaskLayer(name, kind, data, grid_id, band, source)` stores a copied immutable Boolean raster. `True` means membership in the semantic layer; it does not automatically mean reject.
- `SceneState(target_id, grid_id, band, shape, layers, revision)` validates unique names, compatible grid/shape/band, and disjoint *sky-training* and *sky-holdout* regions. `with_layer` / `without_layer` return new revisions, never mutate prior versions.
- `MaskDecision(valid, excluded_by, operation)` contains a read-only usable-pixel raster and a per-reason breakdown. There is no inpainting or signal replacement.
- `MaskEngine(scene).evaluate(observation, operation)` checks the actual `ObservationSet` WCS fingerprint and band; it also honors the observation's existing validity map.

A strict `wcs-sha256` identifier hashes a canonicalized FITS WCS header together with the image shape. It rejects equal-sized images with different WCS. This is **not** a substitute for astronomical reprojection or the geometric tie-point check performed during v0.2 FITS ingestion. Semantically equivalent headers may produce different hashes; that conservative behavior is intentional for this initial implementation.

`SceneState.to_dict()` and `from_dict()` are lossless for the Boolean mask layers, using packed bits in Base64. The schema version is `0.3.0` and unknown versions are rejected. This is a small, auditable transport format for a scene. Large survey outputs will need external FITS storage for mask planes and a separate manifest, rather than embedding every plane into JSON.

## Mask semantics

| Semantic layer | Photometry/geometry | Sky training/holdout |
| --- | --- | --- |
| Nonfinite data, bad inverse variance, no coverage, saturation, instrumental defects | Reject | Reject |
| Star core/wings, neighbor, manual exclusion | Reject | Reject |
| Target protection | Does not override defects or neighbor masks; does not reject by itself | Reject from both sky regions |
| Catalog warnings (e.g. BRIGHT, GALAXY) | Do not reject by themselves | Do not reject by themselves |
| SKY_TRAIN / SKY_HOLDOUT | Merely labels; does not reject | Require corresponding explicit labeled region |

For native Legacy coadds, the v0.3 adapter deliberately treats `SATUR_{band}` and `ALLMASK_{band}` as conservative exclusions even if the v0.2 loader used a more permissive policy. A `SATUR` bit can indicate that at least one exposure was saturated and does **not** by itself prove that all information in the coadd is unusable; alternative scientific policies should be developed and separately tested later. `NPRIMARY` remains a warning, not an assumed absence-of-coverage mask. Actual coverage requires input coverage/quality information.

## Example

```python
from lsbpipe.survey import load_legacy_coadd
from lsbpipe.scene import MaskLayer, LayerKind, Operation, scene_from_observation
from lsbpipe.mask_engine import MaskEngine

obs = load_legacy_coadd('image.fits', 'invvar.fits', 'maskbits.fits', band='r')
scene = scene_from_observation(obs, target_id='galaxy-001')
# User-defined target protection (already aligned with the same observation grid).
protected = make_target_mask(obs)  # returns a 2D boolean raster; user supplies it
scene = scene.with_layer(
    MaskLayer('protected_target', LayerKind.TARGET_PROTECT,
              protected, scene.grid_id, band='r', source='researcher'))
result = MaskEngine(scene).evaluate(obs, Operation.PHOTOMETRY)
# Feed result.valid to a MeasurementFrame rather than modifying obs.image_nMgy.
```

For sky fitting, explicitly add disjoint `SKY_TRAIN` and `SKY_HOLDOUT` layers first, and call `Operation.SKY_FIT` / `Operation.SKY_HOLDOUT`. If those regions are absent or overlap, the operation fails closed.

## Validation and limits

P0a adds seven tests checking hard-mask precedence, sky region separation, warning behavior, versioned serialization, no mutation, signed data, strict WCS/band matching and preservation of v0.2 observation quality.

Local execution without Astropy/Photutils: **59 passed, 1 skipped** (the Astropy-specific integration test module is unavailable here). The user's prior Windows baseline passed **56/56 tests** with Astropy/Photutils; the new seven tests still need to be rerun in that fully provisioned environment. Passing unit tests does not establish the ability to recover real low-surface-brightness emission.

Next increments in the same feature branch: robust constant/planar residual sky candidates; spatial sky holdout QA; fixed-aperture photometric sensitivity, with independent numerical tests for each.
