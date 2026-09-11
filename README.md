# cwhost

Run the CodeWarrior Pro 8 PowerPC C/C++ compiler on a modern machine.

CodeWarrior's Classic Mac OS compiler only exists as an IDE plug-in: a PEF
code fragment that expects to be loaded by the CodeWarrior IDE under Mac OS 9.
`cwhost` loads that plug-in into a PowerPC emulator ([Unicorn]), implements the
IDE callbacks and the handful of Carbon routines it imports, and drives the
compile. The output is byte-identical to what the IDE produces.

`cwhost` contains no Metrowerks code. You need your own CodeWarrior Pro 8
installation; the prebuilt images at [zt-labs/cw-pro-8](https://github.com/zt-labs/cw-pro-8)
are one way to get it.

[Unicorn]: https://www.unicorn-engine.org/

## Requirements

- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/)
- A CodeWarrior Pro 8 `Metrowerks CodeWarrior` folder containing:
  - `CodeWarrior Plugins/Compilers/MW C-C++ PPC` (the 8.0, 8.2 or 8.3 compiler)
  - `MSL/` (MSL C, C++ and Extras headers)
  - `MacOS Support/Universal/Interfaces/CIncludes`

The plug-in keeps its message strings in its resource fork. On macOS the fork
is read directly; on other systems put an AppleDouble sidecar
(`._MW C-C++ PPC`) next to the file, which is what `tar` and `zip` on a Mac
produce.

## Install

```sh
uv tool install cwhost      # gives you the cwcc command
```

or add it to a project with `uv add cwhost`.

## Usage

```sh
export CWHOST_CW_ROOT="/Volumes/CodeWarrior/Metrowerks CodeWarrior"
cwcc -dialect c -opt all -opt speed -align powerpc \
     -prefix "$CWHOST_CW_ROOT/MSL/MSL_C/MSL_MacOS/Include/ansi_prefix.mac.h" \
     -c hello.c -o hello.o
```

`cwcc` takes `mwpefcc`-style flags. Host options:

| Option | Meaning |
| --- | --- |
| `--cw-root PATH` | the `Metrowerks CodeWarrior` folder (or set `CWHOST_CW_ROOT`) |
| `-I DIR`, `-ir DIR` | user and system include directories, in order |
| `-nostdinc` | do not add the root's MSL and Universal Interfaces |
| `-prefix FILE` | prefix header; `-prefix ""` for none |
| `-E` | write preprocessed text instead of an object |
| `-g` | generate debug information |
| `--trace FILE` | log every host callback as JSON lines |

Everything else is a compiler flag and maps onto the IDE's preference panels
(`-dialect`, `-opt`, `-align`, `-inline`, `-w`, `-rtti`, `-cpp_exceptions`,
`-traceback`, `-tocdata`, …). `cwcc --help` lists them. Unknown flags are
rejected rather than ignored.

Exit codes: 0 success, 1 compiler diagnostics, 2 host or configuration error.

### Deterministic builds

The compiler stamps dates into objects. Set `CWHOST_TIMESTAMP` (Unix seconds)
and optionally `CWHOST_TZ_OFFSET` (seconds east of UTC) to fix every clock the
compiler observes.

### As a library

```python
from pathlib import Path
from cwhost.api import CompileOptions, compile

result = compile(
    Path("/Volumes/CodeWarrior/Metrowerks CodeWarrior"),
    Path("hello.c"),
    CompileOptions(
        include_user=[], include_system=[], prefix=Path("ansi_prefix.mac.h")
    ),
)
for message in result.messages:
    print(message.text)
if result.status == 0:
    Path("hello.o").write_bytes(result.output)
```

`compile()` raises `HostError` only for host and configuration failures;
compiler diagnostics come back in the result.

## What works

- The 8.0, 8.2 and 8.3 `MW C-C++ PPC` plug-ins, C and C++.
- Verified by compiling the Metrowerks Standard Library sources and comparing
  every object against the libraries the IDE built. CI does this on every
  change for 8.0 against the library shipped on the CD.

Not supported: the linker (`cwld`), precompiled headers, and IDE callbacks the
compiler has not been observed to call. An unobserved callback fails with
`unimplemented import: <name>` rather than guessing.

## Development

```sh
uv sync
uv run pytest                       # tests that need no CodeWarrior are always run
CWHOST_CW_ROOT=... uv run pytest    # also loads and drives the real plug-in
uv run ruff check && uv run ruff format --check && uv run ty check
```

```sh
# A root from the published image:
rm -rf cw-root
id=$(docker create ghcr.io/zt-labs/cw-pro-8:8.0 /bin/true)
docker cp "$id:/opt/codewarrior/Metrowerks CodeWarrior" cw-root && docker rm "$id"
CWHOST_CW_ROOT=cw-root uv run pytest
```

## Notice

See `NOTICE` for what this project does not include.
