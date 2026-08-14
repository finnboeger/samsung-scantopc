# samsung-scantopc — Docker Container

Runs the [samsung-scantopc](https://github.com/kleest/samsung-scantopc) server
inside a container for easier deployment on a server.  All configuration is done 
through **environment variables**; no config file needs to be bind-mounted.

For advanced scan options that include image filter functions, an optional
Python file can be mounted into the container (see [Custom Options File](#custom-options-file)).

---

## Quick Start

`docker compose build && docker compose up`

---

## Environment Variables

### Core Feature Flags

| Variable | Default | Description |
|---|---|---|
| `ENABLED_SERVER` | `true` | Set to `false` to start the container without registering with the scanner. |
| `MODIFIED_SANE` | `false` | Enable the TCP/UDP proxy mode required for multipage scanning on some models (e.g. CLX-3305W). |
| `PROXY_DEBUGLEVEL` | `1` | Verbosity of the proxy processes. `0` = silent, `1` = light, `2` = moderate, `3` = every packet. Only relevant when `MODIFIED_SANE=true`. |
| `SCANNER_CACHING` | `true` | Keep the SANE connection open between jobs. Set to `false` if you run more than one server against the same scanner. |

### Scanner / Server Identity

| Variable | Default | Description |
|---|---|---|
| `SCANNER_SANE_NAME` | *(auto-detect)* | Full SANE device string, e.g. `smfp:SAMSUNG CLX-3300 Series on 192.168.1.50`. When omitted the server scans for the first Samsung device every 30 s until one appears. Find the correct value with `scanimage -L` on the host. |
| `SERVER_NAME` | *(hostname)* | Name shown in the scanner's "Scan to PC" destination list. |

### File Ownership

These variables are also read by `start.sh` to create the OS user that will
own the scan files.  Both default to `1000` / `scanuser`.

| Variable | Default | Description |
|---|---|---|
| `OWNER_UID` | `1000` | UID of the user that will own saved scan files. |
| `OWNER` | `scanuser` | Username corresponding to `OWNER_UID`. |

### Output Path

The final output path is `SCAN_OUTPUT_DIR/SCAN_FILENAME_TEMPLATE.<ext>`.

| Variable | Default | Description |
|---|---|---|
| `SCAN_OUTPUT_DIR` | `/scans` | Directory inside the container where scans are written. This should match the left-hand side of your volume mount. |
| `SCAN_FILENAME_TEMPLATE` | `SCAN_${date}__${uid}` | Filename template (without extension). Supported substitutions: `${date}` → `YYYY-MM-DD`, `${uid}` → zero-padded sequence number, `${homedir}` → home directory of `OWNER`. |

### Logging

| Variable | Default | Description |
|---|---|---|
| `LOG_NAME` | `/var/log/samsungScannerServer.log` | Absolute path to the rotating log file. Set to an empty string (`LOG_NAME=`) to disable file logging. |
| `LOG_MAXBYTES` | `100000` | Maximum size of the log file before it is rotated. |
| `LOG_BACKUPCOUNT` | `1` | Number of rotated log file backups to keep. |

### Scan Options (JSON)

When neither an [options file](#custom-options-file) nor `SCAN_OPTIONS` is set,
six built-in presets are registered with the scanner (grey/colour × PDF/JPEG at
various DPIs — see source defaults).

To override, set `SCAN_OPTIONS` to a JSON array.  Each object may contain:

| Key | Default | Accepted values |
|---|---|---|
| `name` | `Unnamed` | Any string — shown on the scanner display |
| `color` | `COLOR_GRAY` | `COLOR_GRAY`, `COLOR_TRUE`, `COLOR_MONO` |
| `resolution` | `DPI_300` | `DPI_75`, `DPI_150`, `DPI_200`, `DPI_300`, `DPI_600` |
| `format` | `FORMAT_M_PDF` | `FORMAT_M_PDF`, `FORMAT_S_PDF`, `FORMAT_PDF`, `FORMAT_JPEG`, `FORMAT_M_TIFF`, `FORMAT_S_TIFF` |
| `size` | `SIZE_A4` | `SIZE_A4`, `SIZE_A5`, `SIZE_LETTER`, `SIZE_LEGAL`, `SIZE_B5_JIS`, `SIZE_EXECUTIVE`, `SIZE_FOLIO` |
| `output` | *(derived from* `SCAN_OUTPUT_DIR` *+* `SCAN_FILENAME_TEMPLATE`*)* | Override the output path template for this preset only |

**Note:** `FORMAT_PDF` (single-page PDF) should be used instead of
`FORMAT_M_PDF` / `FORMAT_S_PDF` on some SCX-472x and similar models.

**Example:**

```yaml
environment:
  SCAN_OPTIONS: >-
    [
      {"name": "Color PDF",  "color": "COLOR_TRUE", "resolution": "DPI_300", "format": "FORMAT_M_PDF", "size": "SIZE_A4"},
      {"name": "Gray JPEG",  "color": "COLOR_GRAY", "resolution": "DPI_150", "format": "FORMAT_JPEG",  "size": "SIZE_A4"}
    ]
```

Filter functions **cannot** be expressed in JSON.  Use the
[custom options file](#custom-options-file) for those.

### Advanced: Conversion Tables

These rarely need changing.  Override only if your device reports unusual
SANE mode or size strings.  Both accept a JSON object.

| Variable | Description |
|---|---|
| `MODES2SANE` | Maps scanner colour-mode tokens to SANE mode strings. Default: `{"COLOR_MONO": "Black and White - Line Art", "COLOR_GRAY": "Grayscale - 256 Levels", "COLOR_TRUE": "Color - 16 Million Colors"}` |
| `SIZE2SANE` | Maps scanner size tokens to SANE page-format strings. When unset, the server queries the device's `CAP.XML` and auto-configures this mapping at startup. Example override: `{"SIZE_A4": "A4", "SIZE_LETTER": "Letter - 8.5\"x11\""}` |

---

## Custom Options File

For presets that use image filter functions, create a Python file on the host
and bind-mount it into the container.  The server will `exec` this file in
place of `SCAN_OPTIONS`.

### Mount path

The default path inside the container is `/etc/samsungScannerServer.options.py`.
Override with the `OPTIONS_FILE` environment variable.

```yaml
volumes:
  - ./scans:/scans
  - ./my-options.py:/etc/samsungScannerServer.options.py:ro
```

### File format

The file must define an `OPTIONS` list.  It has access to `OUTPUT_PREFIX` and
anything already imported in the main script (`Image`, `os`, etc.).  Any
additional imports must be done inside the file itself.

```python
# my-options.py
from PIL import ImageOps


def contrast_filter(im):
    """Auto-contrast, clipping the brightest/darkest 10 %."""
    return ImageOps.autocontrast(im, 10)


OPTIONS = [
    {
        "name": "Gray PDF (auto-contrast)",
        "color": "COLOR_GRAY",
        "resolution": "DPI_300",
        "format": "FORMAT_M_PDF",
        "size": "SIZE_A4",
        "output": OUTPUT_PREFIX,   # OUTPUT_PREFIX is available from the parent script
        "filters": [contrast_filter],
    },
    {
        "name": "Color PDF",
        "color": "COLOR_TRUE",
        "resolution": "DPI_300",
        "format": "FORMAT_M_PDF",
        "size": "SIZE_A4",
        "output": OUTPUT_PREFIX,
        "filters": [],
    },
]
```

| Variable | Default | Description |
|---|---|---|
| `OPTIONS_FILE` | `/etc/samsungScannerServer.options.py` | Path to the options Python file. If the file does not exist the server falls back to `SCAN_OPTIONS` or the built-in defaults. |

---

## Volume

| Mount point | Description |
|---|---|
| `/scans` | Output directory for all scanned files.  Mount a host path here, e.g. `./scans:/scans`. |

---

## License
This software is subject to the GNU General Public License v3.0 (GNU GPLv3).

```
Copyright (C) 2022-2023 Steffen Klee
Copyright (C) 2012-2013 angelnu & Totally King

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with this program. If not, see <https://www.gnu.org/licenses/>. 
```
