# Vendored reference sources

These files are **not part of the add-on** and are never installed. They are the
original Descent 3 engine sources for the polygon model format, kept here so the
parser's behaviour can be checked against the code that actually shipped.

| File | What it gives you |
|------|-------------------|
| `polymodel.cpp` | `ReadNewModelFile` (chunk reader) and `SetPolymodelProperties` (the `$rotate=` / `$fov=` property commands) |
| `polymodel.h` | `PM_COMPATIBLE_VERSION`, `PM_OBJFILE_VERSION`, chunk FourCCs |
| `polymodel_external.h` | `bsp_info`, and the `SOF_*` / `PMF_*` flag bits |

`descent3_plugin/poformat.py` cites these by symbol and line — for example
`MIN_OBJFILE_VERSION` is annotated `PM_COMPATIBLE_VERSION (polymodel.h:305)`.
When a citation looks wrong, trust the file here over the comment: line numbers
drift if these are ever re-vendored.

## Licence

> Descent 3
> Copyright (C) 2024 Parallax Software
>
> This program is free software: you can redistribute it and/or modify it under
> the terms of the GNU General Public License as published by the Free Software
> Foundation, either version 3 of the License, or (at your option) any later
> version.

These files are GPL-3.0-or-later and are redistributed here unmodified under
those terms, which is why the add-on itself is GPL-3.0-or-later (see `LICENSE`
at the repository root).
