# LSBpipe — Object-centric Project Hub, project lifecycle and catalog identity
Status: architectural design proposal, 2026-11-01. Not implemented as a launcher.
Target: existing Draft PR #6 (\`feature/science-qa-gui\`) or dedicated downstream feature branch.
No science/photometry acceptance is implied.

## Product decision

The launcher, not the Legacy Viewer, is the application entry point.
**Galaxy / target** is the astronomical entity. **Project** is an immutable
research workspace identified by UUID. **Dataset** is one release/region/
brick/band science quartet with independent archive identity, provenance,
availability and QA. **Run** is a frozen analysis execution with input
fingerprints, parameters and outputs. **Catalog match** is an optional,
mutable external association and NOT the internal identity of the target.

A user's most frequent workflow is one project per galaxy. Do not impose
one-to-one globally: separate studies of one galaxy may need different
analysis configuration, masks, apertures or independent releases. A project
usually has one primary astronomical target but may include several sky
footprints, observations, contaminants and comparison targets.

Projects must work completely offline; an unrecognized or unpublished LSB
galaxy is fully supported without SIMBAD, NED, HyperLEDA or network access.

## Navigation

Launch -> **Project Hub** (no Viewer and no network requests).
Project Hub contains:
- **Recent projects**: searchable by editable name, aliases, sky position,
  survey release and local path; last-opened descending; show status, last
  run, disk availability and selected bands; default no thumbnails requiring
  network or large FITS decoding.
- **New project**: object identifier or user name, optional RA/Dec, project
  folder, optional description; validation; create an *empty* workspace
  immediately, without requiring any original FITS.
- **Import existing folder**: choose known project manifest or legacy
  downloads, show a **read-only preview** of inferred identity, target,
  data releases, number of complete/incomplete quartets, previous runs;
  let the user confirm the import; no redownload or automatic reparenting.
- **Continue last**: a one-click suggested action on the first recent card.
- **Settings**: default new-project location, offline/network behavior,
  optional resolver timeout, disk path management.

Opening a card -> **Project Workspace** (current Survey Explorer, renamed
and restructured). Top context bar displays target/project identity, selected
dataset, local-online state; 'Back to projects' returns to Hub without
closing the application. Project context persists on deliberate changes
and on window close. Opening a project must not automatically run science
jobs, network calls or FITS download.

Do not make a splash modal that obscures error handling: the hub is a
first-class main application page/window with keyboard navigation and
accessible focus states.

## Creation wizard, minimal defaults

Stage A **Object**:
  - Required: editable, unique-within-current-library *display name*.
    Do not infer name from a search result without the user selecting it.
  - Coordinates: accepted ICRS target center in decimal degrees, separately
    from any catalog position. Coordinates may be entered explicitly; if
    unknown, create a draft project with state \`NEEDS_TARGET_POSITION\`
    and disable *spatially sensitive* operations until specified.
  - Optional explicit 'Search SIMBAD' to propose recognized names/positions.
Stage B **Data**:
  - Project root default within user-selected projects library, editable.
  - Optional 'Use existing FITS directory' link, not necessarily a move/copy.
  - Optional initial DR9/DR10/DR11 preferences, bands, nominal TAN field
    width, but no download yet.
Stage C **Review/Create**:
  - Show folder, target coordinate source, selected aliases, initial data
    state and expected offline behavior; create atomically.
  - After creation open project workspace (local dataset inventory may
    initially be empty). Do not require network.
All stages allow backtracking; creation is idempotent within a final
confirmation and refuses overwriting an occupied unrelated directory.

## Data model / durable metadata

Use a new versioned schema, e.g. \`lsbpipe.object-project/1\`, with migrations
from \`lsbpipe.local-project/0.1\`. Never reinterpret older schemas in place
without a dry-run, backup and explicit migration outcome.

\`\`\`text
project_id: UUIDv4 immutable, local—not SIMBAD OID or Legacy brick
display_name: user-editable; aliases: [explicit or catalog-derived]
primary_target:
  ra_deg, dec_deg, frame='ICRS', source ('user' | 'catalog_selected' ...)
  provenance, optional uncertainty, chosen_at; separate catalog astrometry
association:
  provider, external_id, main_id, matched_name, ra_deg, dec_deg,
  type, separation_arcsec, retrieval timestamp, user_confirmed,
  field reference metadata; never replace chosen sky center implicitly
datasets[]:
  survey, release, region, brick, band, product map
  local file presence, size/mtime, checksums if verified, WCS QA status
footprints[]: center, TAN width, science aperture definitions
analysis_runs[]: immutable QA run references, parameter/input fingerprints
session: selected dataset, active workbench/tab, preferences
\`\`\`

Original FITS are immutable and addressed by validated paths. Project
metadata and caches reside outside \`data/\`; project-specific provenance
in the project root. Global index in %LOCALAPPDATA%/LSBpipe stores UUID,
display name, root and last-opened, **not** the source of astronomical truth.
A portable project opens by its own manifest even if the global index is
gone. Missing/moved roots mark 'unavailable'; give 'Relink' without deleting
or silently recreating directories.

Avoid conflating \`project_id\`, galactic object identifier, selected FITS
brick and primary source astrometry. **No filename may determine the exact
target center.** Legacy brick names encode tiling and do not identify a galaxy.

## Catalog-assisted identification (optional SIMBAD adapter)

A SIMBAD integration is an **explicit resolver and metadata suggestion**,
not automatic two-way synchronization or authoritative scientific input.

- For a user-entered name, call \`astroquery.simbad.Simbad.query_object\`
  and optionally \`query_objectids\` in a worker, with timeout, result cap
  and cache. For manually entered coordinates, call \`query_region\` with
  a bounded, user-visible radius, return multiple candidates and their
  angular separation and object types.
- Never accept 'nearest sky object' blindly. Nearby stars, a galaxy nucleus
  and extended low-surface-brightness objects may have distinct SIMBAD
  associations and centers. Offer explicit 'Use catalog position' as a
  separately confirmed change, with before/after coordinate preview.
- Search results display provider, main_id, alternate ids, object type,
  catalog ICRS position, angular offset from the chosen analysis center,
  retrieved timestamp, and uncertainty/source metadata where available.
- States: NOT_QUERIED, FOUND_UNCONFIRMED, USER_CONFIRMED, AMBIGUOUS,
  NO_MATCH, OFFLINE, SERVICE_ERROR; none should prevent project creation.
- Store the retrieved snapshot and provider provenance; never update a
  measurement/velocity/redshift/size merely because the source changed.
  Photometry and distance are adopted only through separate scientific
  provenance/uncertainty procedures, not by using SIMBAD defaults.
- No network during startup, project browse or local FITS import.
  Search is an explicit action; refresh is explicit, with a visible
  difference diff/accept flow; use a polite bounded request rate, identify
  the client, cache repeat queries. Make \`astroquery\` an optional package
  extra, e.g. \`[catalog]\`, not part of the offline GUI requirement.
- The CDS SIMBAD database is a heterogeneous literature meta-compilation,
  not a homogeneous photometric measurement catalog; unlabeled values are
  not science-ready. For extragalactic measurements an eventual optional
  NED/HyperLEDA adapter can share the *resolver interface* but must keep
  independent source attribution and conflicting values.

Official reference:
- https://simbad.cds.unistra.fr/simbad/
- https://docs.astropy.org/projects/astroquery/en/stable/simbad/simbad.html
- https://docs.astropy.org/projects/astroquery/en/latest/simbad/simbad_evolution.html
The astroquery 0.4.8+ SIMBAD implementation uses TAP and lowercase column
names; avoid hardcoding older pre-TAP result fields.

## Migration from currently downloaded UGC_2162

Previous original FITS are already at
\`C:\Users\frigi\Documents\PhD\LSBpipeData\UGC_2162\` and the object
search center is RA 40.096208 deg, Dec +1.229250 deg, TAN width 5'.
This is user-established metadata, **not** a verified SIMBAD cross-match.

Show 'Import existing' with discovery of \`data/dr9/<region>/...\` as well
as direct \`dr9/<region>/...\`. Recover field from existing
\`lsbpipe-project.json\` first, then \`download-report.json\`, otherwise
present explicit coordinate confirmation. Capture selected North/South and
band; continue using original paths. Index \`qa-runs\` manifests as prior
**diagnostics**, not as revalidated accepted science. Preserve previous
project files and previous QA manifests; migrate \`0.1\` to new identity
without modifying original FITS.

The existing \`open_local_project\` cannot create a zero-FITS project;
new-project metadata initialization must be decoupled from inventory
scanning. Existing \`SurveyExplorer.__init__\` immediately builds the
QWebEngine workspace and conditionally restores last project: refactor
\`main\` to launch the Hub first and instantiate Explorer only after
selection. The old GUI's download/search screen becomes a child workbench
within the selected project. Old auto-resume becomes 'Continue last' on
Hub; legacy CLI remains supported.

## Fault handling / consistency

- Partial/corrupt manifest: do not pretend normal resume; offer backup,
  dry-run recovery, explicit user decision, never overwrite corrupt file.
- Duplicate galaxy names: warn, offer existing project or separate
  independent UUID; do not merge by proximity or by SIMBAD alias.
- External network errors: keep explicit typed coordinates and local work.
- Crash during import: atomic metadata write, never delete local files.
- Interrupted QA: distinguish incomplete staging from immutable completed
  records. Do not display a partial result as completed.
- Switching projects while a QThread is running: block switch or request
  cooperative cancellation, then clean thread shutdown. Every callback
  must re-check project UUID and frozen run context; main-thread Qt slots.
- Python tests must not overwrite the real %LOCALAPPDATA% project index.
- Project removal from recent list ≠ delete on-disk project; deletion
  requires a separate explicit confirmation and should not be in initial MVP.
- Separately expose local availability, FITS integrity checks, WCS QA,
  science readiness and adopted measurements. Neither local files nor
  SIMBAD match imply scientific acceptance.

## Planned implementation boundaries

1. v1 launcher: Project Hub, Recent/Open/Create, empty projects, portable
   versioned metadata, global registry, migrations, offline reopen.
2. Workspace refactor: explicit active UUID, datasets list, workbench
   switching, no hidden remote Viewer startup, project-state save.
3. Diagnostics integration: QA history and immutable manifest selection,
   incomplete work visibility, dataset-level masks/coverage.
4. Optional SIMBAD resolver UI with deterministic mocked offline tests,
   explicit catalog identity acceptance and provenance.
5. Future multi-project browsing, collections/target lists, comparison
   science, catalog enrichment from independent NED/HyperLEDA sources.

## Acceptance tests

- First launch on empty machine shows Hub and zero projects, no web calls.
- Create named project without FITS and without SIMBAD; reopen offline.
- Enter ICRS center and create project, verify exact target persistence.
- Import UGC_2162 from existing directory, preserve byte-level original FITS,
  recognize North/South original quartets and prior QA history.
- Reopen on next launch: Hub shows UGC_2162 as last project, not Explorer
  without explicit user action.
- Rename project without changing identity, scientific center or file paths.
- Two projects can point at same galaxy without overwriting each other.
- Missing project folder is marked unavailable and can be relinked.
- SIMBAD no-match, ambiguous multi-match, timeout and offline cases never
  block project creation; nearest candidate is not auto-selected.
- Returning from active project to Hub leaves science files untouched and
  does not revive stale background callbacks.
- GUI tests on Qt Windows with metadata migrations, Unicode names, settings
  isolation, long project lists and DPI/keyboard accessibility.
