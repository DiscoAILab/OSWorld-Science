# probe/ — the ground-truth probes for verify_image.py

Acceptance items (9) and (10) of `../verify_image.py` upload these two files
into the guest and read the calibration values back with the guest's own
GDAL/OGR, comparing them with known truth. That checks the whole path
"upload → on disk → read by the geo libraries" end to end without touching
any task asset (no licence coupling). The recipe that generates them lives in
`make_probe.py`; this file is the truth table.

## Truth table

Three places must agree: this table, `TRUTH` in `make_probe.py`, and
`TIF_TRUTH` / `GPKG_TRUTH` in `../verify_image.py`.

| file | item | truth |
|---|---|---|
| probe.tif | size / bands | 64×64, one UInt16 band |
| probe.tif | CRS | EPSG:32633 (UTM 33N) |
| probe.tif | GeoTransform | (500000, 10, 0, 4649776, 0, -10) |
| probe.tif | pixel values | v(row, col) = row + col |
| probe.tif | band checksum | 48341 |
| probe.gpkg | layer | probe_units (Polygon Z, z=0) |
| probe.gpkg | features | three 80×80 m squares: y0=4649000, x0=500000/500100/500200, unit_id=1..3 |
| probe.gpkg | CRS | EPSG:32633 |

The checksum is GDAL's rotating prime-modulus sum (gdalchecksum.cpp, the
eleven primes {7, 11, 13, …, 43}), not a plain sum, so it is fixed by the
pixel formula alone; the vector truth is fixed by the feature definitions.
The recipe parameters in `make_probe.py` therefore *are* the definition of
the truth.

## Regenerating

In the guest, or on any machine with osgeo (the GDAL Python bindings):

```bash
python3 make_probe.py            # writes into this directory (overwrites the probes)
python3 make_probe.py /tmp/out   # or write elsewhere first and compare
```

The script reads the files back immediately and exits non-zero if they do
not match `TRUTH`. Byte-for-byte reproduction is **not** guaranteed
(GeoPackage carries timestamps and other volatile metadata; the existing
probe stores `unit_id` as MEDIUMINT while a regenerated one may say INTEGER,
which SQLite affinity treats alike). Equivalence means the truth table above.

## Keeping the three places in sync

Changing a recipe parameter (size, EPSG, GeoTransform, pixel formula,
feature definitions) changes the truth, so all three must change together:

1. `TRUTH` in `make_probe.py` (otherwise its self-check stops you first);
2. `TIF_TRUTH` / `GPKG_TRUTH` in `../verify_image.py` (the error messages are
   built from those dicts, so the dicts are the only place to edit);
3. the truth table in this README.

Replacing the files without touching the recipe (an equivalent regeneration)
needs no truth change anywhere.
