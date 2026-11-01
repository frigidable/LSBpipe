# P0.1: one authoritative object passport after legacy FITS import

Scope: `feature/science-qa-gui` / Draft PR #6. This repair concerns
project consistency only. It does not change numerical photometry, mask
physics or the bytes of original FITS.

## Provenance and ownership

- `lsbpipe-object.json` (`lsbpipe.object-project/1`) owns the primary
  ICRS center, square width, selected release/region/brick/band, adopted
  QA screening radii, project UUID and last QA reference once the folder
  has been imported as an object project.
- `lsbpipe-project.json` (`lsbpipe.local-project/0.1`) is legacy
  metadata. Older standalone Survey Explorer keeps its own old writer.
  The object-project workspace must never rewrite this legacy file.
- `download-report.json` is download provenance only. Its historical
  target field cannot overwrite an accepted object-project center.
- Original native Legacy FITS remain read-only. Import and reopen
  only inventory their paths; neither rehash nor science-validity
  acceptance follows from their mere presence.
- If an older `lsbpipe-object.json` omits QA radii, use the previous
  documented 60/90/180 arcsec defaults for backward compatibility,
  without modifying the object file until an explicit save.
- On first migration, import target and radii from the pre-existing
  legacy state as an initial one-time snapshot; later reopen always
  prefers the new passport. This prevents a formerly imported project
  from reverting after independent center adjustments.
- The chosen dataset is honored when available in local inventory. If
  it is missing, the inventory can show a present fallback candidate;
  the program must never silently take a contradictory old selection.
- Reselection after an archive download uses the active object position
  rather than importing the old 0.1 target from disk.
- If both ICRS fields are intentionally blank, a draft can persist
  `field=null`. Invalid half-entered coordinates fail validation and
  cannot silently clear previously accepted coordinates.
- The saved last QA report is historical diagnostic evidence; it is
  displayed only on a matching field/dataset/context and explicitly
  marked as not revalidated.

## Regression requirements

1. Create an empty named galaxy with no FITS: reopen retains UUID and
   no accepted coordinates, and preserves QA screening radii.
2. Import old UGC_2162 with DR9 North/South: new manifest is a companion,
   all original FITS and legacy `lsbpipe-project.json` bytes remain
   unchanged.
3. Change the accepted ICRS center and selected region in the object
   passport while deliberately retaining the old target and band in
   legacy JSON. Reopen both with direct backend and the real Qt GUI.
   Confirm new ICRS and selection win; old files byte-identical.
4. Change accepted radii in the GUI, return to Hub, reopen and verify
   the exact values.
5. If the project-selected dataset no longer exists locally, explicitly
   identify the local fallback without adopting a stale legacy selection.
6. A raw legacy standalone session must continue to work as before.

## Still separate future gates

- Per-project immutable parameter edit history and a full Run DAG.
- UUID of project in the frozen scientific QA context.
- Per-project QA-root selection for external FITS libraries.
- Moved project repair for external data roots and resilient loader
  behavior with corrupt obsolete legacy metadata.
- Real Windows manual original-FITS smoke test and interruption
  stress testing; offline CI alone is insufficient.
