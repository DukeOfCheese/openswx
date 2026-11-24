# SOLIDWORKS 2025 interoperability findings



Source: SOLIDWORKS 2025 SP05, analysed from the local install with REA and read-only metadata tools.

Scope: architecture, public API surface and file format, i.e. the facts openswx needs to interoperate.

No SOLIDWORKS implementation code is reproduced here or anywhere in openswx; implementation is

written from these specs (clean room). Do not paste decompiled code into this repository.



## 1. Architecture (module inventory)



3,411 PE modules under `SOLIDWORKS Corp\SOLIDWORKS` (7.1 GB). The core is a Dassault application

layer over licensed third-party engines:



| Subsystem | Module(s) | Vendor | openswx equivalent |

|---|---|---|---|

| B-rep kernel | `pskernel.dll` (Parasolid 36.01) | Siemens | OpenCASCADE (OCCT) |

| 2D sketch solver | `dcu2d77.dll` (2D DCM), `dcupgm77.dll` (PGM) | Siemens D-Cubed | PlaneGCS or `libslvs` (SolveSpace) |

| Assembly mates | `dcu3d62.dll` (3D DCM), `dcuaem62.dll` (AEM), `dcucdm62.dll` (CDM) | Siemens D-Cubed | `libslvs` 3D / custom |

| Translators | `SPAAcis*.dll`, `spiop\` | Spatial (ACIS/InterOp) | OCCT STEP/IGES, `ifcopenshell` |

| 3D PDF / publish | `HoopsPublish\` | Tech Soft 3D | n/a (later) |

| DWG/DXF | ODA libraries, `slddwgu.dll` | Open Design Alliance | `ezdxf` / LibreDWG |

| Linear algebra | MKL | Intel | NumPy / Eigen |

| Application | `sldmodu.dll`, `slduiu.dll`, `sldworks` | Dassault | openswx core |



## 2. Public API surface



Extracted from the shipped interop assemblies in `api\redist\` (metadata only):



| Assembly | Contents |

|---|---|

| `SolidWorks.Interop.sldworks` | 1,024 interfaces, 10,112 methods, 3,498 properties |

| `SolidWorks.Interop.swconst` | 973 enums, 8,199 values |

| `SolidWorks.Interop.swpublished` | 62 add-in / callback interfaces |



Largest interfaces: `IModelDoc2` (744 methods), `IView` (434), `ISldWorks` (330),

`IModelDocExtension` (318), `IFeatureManager` (266), `IBody2` (210), `IAssemblyDoc` (198).



Pattern: methods are versioned by numeric suffix (`FeatureExtrusion`, `...2`, `...3`); only the

newest needs implementing in a compat layer. Feature creation is selection-driven (select sketch,

then call `IFeatureManager.FeatureXxx(...)` with a flat list of options). Example:

`FeatureExtrusion3` takes 23 arguments: single/both directions, flip, end condition per direction

(`swEndConditions_e`), depths, draft per direction, merge, feature scope, start condition and offset.



Enums that a compatible API must accept with identical integer values, e.g.:

- `swEndConditions_e`: Blind=0, ThroughAll=1, ThroughNext=2, UpToVertex=3, UpToSurface=4,

  OffsetFromSurface=5, MidPlane=6, UpToBody=7, ThroughAllBoth=9, UpToSelection=10, UpToNext=11

- `swDocumentTypes_e`: NONE=0, PART=1, ASSEMBLY=2, DRAWING=3

- `swConstraintType_e`: sketch relations (DISTANCE=1 ... COINCIDENT=9, CONCENTRIC=10, ...)



Implication for openswx: expose an MCP tool set modelled on these concepts (sketch entities,

relations, feature definitions, end conditions) so agents trained on SOLIDWORKS macros map cleanly.



## 3. Native file format (.SLDPRT / .SLDASM / .SLDDRW, 2015+)



### 3.1 Container: obfuscated OPC/ZIP



The file is an Open Packaging Conventions package (`[Content_Types].xml`, `_rels/.rels`,

`docProps/*.xml`) stored as a sequence of ZIP-like local records with no central directory:



```

offset  size  field

0       4     record tag (replaces ZIP "PK\3\4"; varies)

4       ...   (first record has extra preamble bytes)

+0      2     version needed = 0x0014

+2      2     flags          = 0x0006

+4      2     method         = 0x0008 (raw deflate)

+6      4     DOS time/date  (constant in samples)

+10     4     CRC-32 of uncompressed payload

+14     4     compressed size

+18     4     uncompressed size

+22     4     name length N

+26     N     name, each byte nibble-swapped: b' = (b << 4 | b >> 4) & 0xFF

+26+N   ...   raw deflate payload (compressed size bytes)

```



Records are found by scanning for `14 00 06 00 08 00`; false positives inside compressed data are

rejected by CRC-32 and size checks. Verified: every stream of the sample files passes.



### 3.2 Streams (part file)



| Stream | Content |

|---|---|

| `Contents/Config-<n>` | serialized model object graph (class tags `moPart_c`, ...) |

| `Contents/Config-<n>-Partition` | **Parasolid partition (B-rep)**, see 3.3 |

| `Config-<n>-FeatureBodies/LocalBodies` | **Parasolid bodies**, see 3.3 |

| `Contents/Config-<n>-ResolvedFeatures` | feature tree records |

| `Contents/DisplayLists` | cached tessellation for display |

| `Contents/CMgr`, `CMgrHdr2`, `CnfgObjs` | configuration manager |

| `Contents/CusProps`, `docProps/custom.xml` | custom properties |

| `swXmlContents/Features` | XML summary: models, configurations, external refs |

| `PreviewPNG`, `Preview` | thumbnail (PNG, DIB) |

| `_MO_VERSION_<v>/Biography`, `History` | save history |

| `Contents/3DExperienceExchange*` | embedded UnQLite databases |



Serialized objects use an MFC-style class tag: `FF FF 01 00 <u16 len> <ascii class name>`.



### 3.3 Geometry: Parasolid binary transmit



`Config-<n>-Partition` and `LocalBodies` contain zlib streams (`78 01` / `78 9C`) that inflate to

**Parasolid binary transmit (XT) data**:



```

PS\0\0\0?: TRANSMIT FILE (partition) created by modeller version 3601212 ... SCH_3601212_36001_13006

PS\0\0\0<: TRANSMIT FILE (deltas)    ...   (rollback deltas)

PS\0\0\03: TRANSMIT FILE              ...   (bodies)

```



Wrapper before each zlib stream (Partition): `u32 size, 16-byte type GUID

23 1d d5 71 da 81 48 a2 a8 58 98 b2 1b 89 ef 99, u32, u32 compressed length`, then zlib.



The XT format is publicly documented by Siemens (Parasolid XT Format Reference) for data exchange.



### 3.4 XT details settled against SOLIDWORKS 2025 exports (Parasolid 36.1)



Implemented in `openswx/exchange/parasolid.py` from the 2006 XT Format Reference; points the reference leaves open,

settled on real files (`tools/difftest/xt_samples.py` exports each difftest part as .x_t/.x_b/.sldprt with

SOLIDWORKS' own mass properties as ground truth):



- Schema `SCH_3601228_36001_13006`: embedded edits against base 13006. After the schema string come the

  maximum node-type count (231; binary short) and then the user-field size (binary 4-byte int).

- Binary prefix: `PS\0\0`, modeller-version length as a 2-byte short, schema length as a 4-byte int.

  Edit-script counts and 255 markers are single bytes; field `n_elts` uses the pointer-index encoding,

  `ptr_class` is a plain short.

- Edit scripts may end (`Z`) with base fields left over: those are implicitly deleted (LIST, type 70).

- CONE: the radius grows along **+axis** (the reference's formula has the sign the other way).

- Rational B-spline vertices are homogeneous (w*x, w*y, w*z, w). B-surface vertices are stored with the

  v index varying fastest (verified: INTERSECTION chart points lie on the surface to 1e-12 mm).

- INTERSECTION curves carry only a chart polyline (3 points for a straight edge); the reader recomputes them

  exactly by surface/surface intersection and keeps the branch through the chart.

- Rolling-ball fillets (BLENDED_EDGE, type 56): `range` = [-r, +r] metres; the spine is an INTERSECTION of
  the two supports offset by the range, and its chart often runs past a support's knot range: Parasolid
  evaluates B-surfaces there by continuing the end polynomials ("implicit extension", SURFACE_DATA unchanged).
  Contact edges are INTERSECTIONs with BLEND_BOUND (59) surfaces; LIMITs of type `L` at chart ends are real
  vertices for edges but artificial ends for spines.
- `.sldasm`: `swXmlContents/COMPINSTANCETREE` (plain XML) lists swFile (doc type, absolute path), swModel
  (configuration id, bounding box in m) and per-component swReference (swTransform: row-major 4x4 in m:
  three rows of [component axis, 0], then [translation, 1]; nested swModels for subassemblies).
- Mates (`Contents/Config-<n>-MatesList`, MFC-style serialisation: a class name is written once, later
  objects of the class follow untagged): per mate a name, then pairs of a UTF-16 face reference such as
  `moFromSktEntSurfIdRep_c,22,3` / `moEndFaceSurfIdRep_c,22,1,0` and a component `name-1@assembly`. The
  numbers are a feature id and payload that match the `FACE_ID_2001` Parasolid attribute on the part's
  faces: records of (code, feature id, time stamp, 0, payload...), e.g. 74 = face from sketch entity k,
  40 = end cap; prefixed records mark later modifying features (a shell's inner copies).
- Helix forms: type 163 is the SURFACE helix form (axis_pt, axis_dir, hand, turns, pitch, gap, tol) and 184 the
  CURVE form (axis_pt, axis_dir, point, hand, turns, pitch, tol): the reference has the two swapped.
- B-surfaces flagged periodic are stored unwrapped; faces bounded only by ring edges need them treated as
  periodic. Null knot entries are padding. Large transmit files are split over consecutive zlib chunks
  (8-byte header each: uncompressed size, compressed size; the first 1 MiB, then 4 KiB pieces).
- XT faces on periodic surfaces whose loop runs along the surface seam (e.g. a cylinder split into two
  halves) get pcurves on the wrong turn from a plain ShapeFix (negative face area); rebuilding them on the
  surface rotated half a turn about its axis gives consistent pcurves.
- Some parts keep an empty partition (WORLD only) and store their bodies as plain body transmit files in
  `Config-<n>-FeatureBodies/LocalBodies`, one stream per configuration. Checked against SOLIDWORKS's own body
  list (2026-11-23): usually one transmit per solid body (disk_experiment: 6 transmits, 6 bodies; cam roller:
  9 and 9, volumes summing to SOLIDWORKS's). Exception: in the plastics sample `wrench` the active
  configuration's stream holds a first transmit with two solids (277.5 mm3) that SOLIDWORKS does not list as
  bodies, then the part (2168.46 mm3, matching SOLIDWORKS); what marks the first as not a body is not known
  yet. The handle and brush_handle samples hold a transmit the reader cannot parse (an intersection curve whose
  interpolation fails).
- A face on a periodic surface that a split line cuts along the seam (part3: half-cylinder strips) can come
  out of the reader with its pcurves on opposite turns, area zero or negative; rebuilding it on the surface
  turned half a revolution gives the true face (it must stay within its edges' box).
- Native reader over the 841 installed SOLIDWORKS 2025 sample parts: 814 read (96.8%), 736 valid solids;
  the rest are surface-only parts, honest failures (geometry that cannot be rebuilt) or timeouts.
- Feature tree (`Contents/Config-<n>-ResolvedFeatures`; method: pairs of parts built in SOLIDWORKS that
  differ in one parameter, diffed byte by byte, `tools/difftest/feature_samples.py`; reader in
  `openswx/exchange/sw_tree.py`, `openswx/exchange/sw_sketch.py`, rebuild in `openswx/exchange/sw_rebuild.py`):
  - MFC-style archive. A new class is `FF FF`, schema word (1), length word, ASCII class name; later objects
    of a class start with its class word `0x8000 | index` (indices are archive-wide). Strings are Unicode
    CStrings `FF FE FF`, length byte, UTF-16.
  - Every tree object (folder, plane, sketch, feature, dimension) is a class word (`04 80` in current files,
    `05 80` in some older ones: the word after the first class tag) followed by its name. Untagged objects
    are typed by their class word, falling back to SOLIDWORKS default names (Sketch<n>, Boss-Extrude<n>, ...).
  - From the end of the name: u32 feature id at +8 (the id the Parasolid faces carry in FACE_ID_2001);
    a dimension's value at +22: a double (metres; radians for `moAngleParameter_c`) or, for counts
    (`moIntegerParameter_c`), a u32. Dimensions follow the feature that owns them, in order (an extrude:
    depth, then draft angle; a linear pattern: count, spacing, count 2, spacing 2).
  - End spec (`moEndSpec_c` or its class word): `00 00 01 00`, 12 bytes of 0/1 flags, then the end condition
    as a u32 (swEndConditions_e: 0 Blind, 1 Through All, 6 Mid Plane, ...). The 7th flag byte (offset 10
    from the start of the spec) is 1 when the direction is reversed (Reverse Direction), 0 otherwise.
  - Classes: `moExtrusion_c` (boss), `moICE_c` (cut-extrude), `moRevolution_c`, `Fillet_c`, `Chamfer_c`,
    `moShell_c`, `moLPattern_c`, `moPlanarSurface_c`, `moProfileFeature_c` (sketch), `moRefPlane_c`, ...
  - Sketch points: u32 id, header `FF FF <version u16> <byte> FF x8 00 00 80 BF ...` (in older files, version
    7, the eight bytes are `FF FF FF FF 00 00 00 00`; some records have 4 zero bytes in place of
    `FF FF <version u16>`), then `00 00 F0 3F`,
    8 zero bytes, a tag `1x 00` (1E plain, 1A etc. with other flags), x and y doubles (metres, sketch
    coordinates), then a u16 kind and a u16 count. Kind 0 with references: per segment using the point, its
    class (`sgLineHandle`, `sgArcHandle`, `sgSplineHandle`; defined inline the first time) + u16 segment
    index + `FF FF FF FF 00 00 00 00`. Kind 0 followed by 4 zero bytes, the count again and `FE FF FF FF`:
    the centre of `count` arcs / circles. Kind 4: a spline through point (in order). A circle repeats its
    one point; an arc's start is its first point record (records may be out of id order after edits, so
    the rebuild checks the sweep side against the stored body).
  - Spline curves (`modifSplineList_c`): u32 knot count + knots, u32 pole count + poles (x, y, z doubles,
    metres); degree = knots - poles - 1.
  - Sketch frame: `50 46 00 00`, u32, `1F 8E 1F 6A 00 00`, u32, a flag byte and, when 1, a row-major 3x3
    rotation (model = R * sketch; columns = sketch x, y, normal), translation (metres) and scale. A sketch
    on a face has no such frame; `FaceRefPlnData_c` holds the plane origin (3 doubles), normal (3 doubles),
    a flag byte and the same rotation.
  - Reference planes (`moRefPlane_c` with `moFaceRefPlnData_c`): origin, normal, flag, rotation as in
    `FaceRefPlnData_c`; a sketch whose frame record has flag 0 lies on the Front plane or on such a plane
    (the one defined just before it, in the samples).
  - Segment records follow the points with the same header; where a point has its tag and coordinates a
    segment has two u16 end points: indices into the sketch's point records in file order (every record
    with a real id, not 0xFFFE / 0xFFFFFFFF, with or without coordinates). The u16 20 bytes after the
    header: low 3 bits the kind (4 / 5 for lines), bit 0x08 construction. Some files keep the point-to-
    segment references only there (points then carry a count but no list); a reference list can also
    hold a null word before a reference.
  - The scale feature (`moScale_c`) stores its x, y, z factors as three consecutive doubles.
  - Sketch relations (in the sketch object): `FF FF FF FF`, 17 zero bytes, the relation type as a u32 (the
    public swConstraintType_e codes: 4 horizontal, 5 vertical, 6 tangent, 7 parallel, 8 perpendicular,
    9 coincident, 10 concentric, 12 midpoint, 14 equal, 17 fixed, ...), a u32 2, `00 00 FE FF 00 00`, then
    the references as in a point's list (class word or inline class, u16 index, `FF FF FF FF 00 00 00 00`).
    Found with parts that differ in one relation only (automatic relations off: SketchManager.AddToDB).
  - A draft (`moDraft_c`) has its angle as a parameter of the length class (the double is in radians). The
    drafted faces keep the FACE_ID of the feature that made them; the draft's own id is not on them.
  - An extrude in two directions has no single end spec found by the reader; its two depths are its first
    two length parameters. A thin extrude's parameters: depth, wall thickness, then (with auto-fillet
    corners) the corner radius; the sketch's corners are rounded with it before the wall is thickened.
  - Older parts name features `Extrude<n>` (boss or cut), `Base-Extrude`, `Base-Revolve`, `Boss-Revolve<n>`
    and may write them without a class tag. Objects with their own names and no tag are typed by content:
    one holding sketch point records is a sketch; one right after a sketch holding an end spec, an extrude.
  - Older sheet metal: the thickness is a parameter of `moSheetMetal_c` (before the base flange), the bend
    radius and depth parameters of the `moSketchBend_c` (BaseBend) after it; the base flange's sketch can
    be an open chain (a thin wall with the bend radius at its corners).
  - Weldment structural members (`moWeldMemberFeat_c`): which path lines a member uses and its profile are
    not decoded; members are separate bodies, mitred on the bisector plane where two members meet.
    A path can hold arcs (curved members); several members can run along one path sketch.
  - A centre rectangle's diagonals can come without the construction flag (lines whose ends both meet two
    or more other segments).
  - Some older fillets have no radius parameter; the faces they made carry the fillet's id in their
    FACE_ID records (after the id of a feature that changed them later), and their radius is the fillet's.
  - Routing connection points (`moConnectionPoint_c`) and route points (`moRoutePoint_c`) hold no geometry.
  - 3D sketches (`mo3DProfileFeature_c`): point records as in 2D sketches with the tag `0E 00` and x, y, z
    (metres, model coordinates). A point's reference list names at most two segments whatever its count;
    the segment records after the points give every segment (two u16 indices into the point records in
    file order, kind 4 for a line), as for 2D sketches.
  - Sketch dimensions (`sgPntPntDist`, `sgLLDist`, `sgPntLineDist`, `sgCircleDim`, `sgAnglDim`, ...) follow
    the sketch; each is written twice as consecutive parameter objects (value double at name + 22), then
    `01 00 00 00`, u16 reference count, u16 flag, 4 zero bytes and the references (class word: line / arc
    handle, or a point handle indexing the sketch's point records; u16 index; FF FF FF FF 00 00 00 00).
  - Not decoded yet (settled against the stored body instead): fillet / chamfer edges,
    shell faces, pattern direction and seed features, revolve axis when it is not a sketch centerline.
- `.sldprt`: `Contents/Config-<n>-Partition` holds a *partition* transmit (root WORLD, type 101) whose BODY

  nodes are the part's bodies; the second zlib stream in that record is a *deltas* transmit (rollback).

- Result: 16/16 parts (planes, cylinders, cones, spheres, tori, B-spline extrusions and revolutions, a drafted

  B-surface with intersection edges, fillets,

  chamfers, shells, patterns) match SOLIDWORKS volume and area from .x_t, .x_b and .sldprt.

An openswx importer can therefore read real SOLIDWORKS geometry: container -> zlib -> XT parser

(written from the public spec) -> OCCT B-rep. The parametric feature history (`Config-<n>`,

`ResolvedFeatures`) is proprietary and undocumented; it is being decoded (see the feature tree notes above).



## 4. Open questions / next probes



- XT binary parser: map schema `SCH_3601212` node types to OCCT topology; validate by comparing

  volume / area / face count against STEP exported by SOLIDWORKS via its API.

- Field layout of the 16-byte-GUID wrapper (meaning of the two u32 values).

- Assembly (`.SLDASM`) streams: component references and mate storage.

- Feature tree: extrude direction flags, fillet / shell selections, sheet metal, sweeps / lofts, holes.

