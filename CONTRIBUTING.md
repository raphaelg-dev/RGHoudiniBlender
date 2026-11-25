# Contributing

How to build, test and change RG Houdini Bridge. How the code is laid out, and where to add something, is in [DEVELOPMENT.md](DEVELOPMENT.md).

Issues are open for bugs: the form asks for the versions, the steps and the content of *Show Log*. Pull requests are read, but nothing guarantees they will be merged: the project is made to be forked.

## Requirements

- Blender 5.2, to build the extension and to run the Blender suites.
- Houdini 20.0 or later with `hython`, for every suite but the pure tests: the server needs Python 3.10, and `hython_entry.py` stops an older one with one line. The tool icons need Houdini's PySide6. Only Houdini 22.0 has been tested.
- [uv](https://docs.astral.sh/uv/), to run ruff with `uvx`. Nothing else is installed: the tests run with Blender's own Python, which has numpy.

On Windows, run Blender's Python by its full path (`C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe`): a bare `python` may be the WindowsApps alias.

## Build and install

```powershell
.\build.ps1                    # build dist/rg_houdini_bridge-<version>.zip and install it in Blender's user_default repository
.\build.ps1 -NoInstall         # build only
.\build.ps1 -Blender <path>    # use another blender.exe
```

Where PowerShell refuses to run scripts, use `powershell -ExecutionPolicy Bypass -File build.ps1`.

On Linux or macOS, or without PowerShell, the build is three commands from the root of the repository (`blender` being Blender 5.2), then the zip installs from *Get Extensions > Install from Disk*:

```sh
mkdir -p dist
cp LICENSE addon/LICENSE
blender --factory-startup --command extension build --source-dir addon --output-dir dist
```

Remove the copy of `LICENSE` in `addon/` afterwards: the zip needs it, the repository keeps it at the root. More on the build in [docs/dev/testing.md](docs/dev/testing.md#building). The Blender suites test the **installed** extension: build and install before running them.

To publish a release, raise `version` in `addon/blender_manifest.toml`, give it a `## <version>` section in `CHANGELOG.md`, and push a tag `v<version>`: `.github/workflows/release.yml` builds the zip, validates it and publishes the release with that section as its notes. A tag that does not match the manifest's version fails.

## Tests

```powershell
$python = "C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe"
$blender = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
& $python -m unittest discover -s tests/unit -t .          # pure tests
& $python tests/test_server.py                             # the hython server alone
& $blender --background --factory-startup --python tests/test_blender.py
& $blender --factory-startup --python tests/test_ui.py     # opens a window
& $blender --factory-startup --enable-event-simulate --python tests/test_ui.py     # the group selection alone
uvx ruff check .
```

| Suite | Needs | About |
|---|---|---|
| `tests/unit/` | any Python with numpy: neither Blender nor Houdini | 6 s |
| `tests/test_server.py` | Houdini, any Python with numpy | 20 s |
| `tests/test_blender.py` | Blender, Houdini, the installed extension | 35 s |
| `tests/test_ui.py` | Blender with a window, Houdini, the installed extension | 80 s, then 50 s with `--enable-event-simulate` (the group selection alone) |

Times are those of the development machine, a Houdini already warm. Each suite ends with exit code 0 when it passes and prints what it checked.

Run one Houdini suite at a time: each starts its own hython, which holds a Houdini licence while it runs. When a suite ends, no hython is left.

The CI (`.github/workflows/ci.yml`) runs ruff and the pure tests on every push and pull request, and builds the extension with Blender 5.2 on Linux, which validates its manifest. The other suites need Blender and Houdini, so run them on your machine before you propose a change.

## Architecture rules

`tests/unit/test_architecture.py` checks them on the source, with no tolerance:

- imports go one way: `shared/` → `blender/core/` → `blender/features/` → `blender/ui/`, and `houdini/` (hython) imports only `shared/` and itself, never Blender;
- no import of the package inside a function, and no cycle;
- the cook engine is reached through its facade, `blender/core/cook/__init__.py`;
- no recursive closure on the Blender side, and no operator named that nobody defines.

[DEVELOPMENT.md](DEVELOPMENT.md#rules) has the whole list and what a failure says.

## Frozen names

Some names live in users' files, keymaps and preferences, or on the wire between Blender and hython. From 1.0.0 on, they are never renamed:

- the operators, panels, menus and the asset shelf (`houdini_bridge.*`, `HOUDINI_BRIDGE_*`), the `PropertyGroup` classes and their properties, the properties attached to Blender's types (`GeometryNodeTree.houdini_bridge`…), the preferences and the hotkeys;
- the values stored in `.blend` files: the parameter kinds, the stream names, the names of the node groups and modifiers the extension makes;
- the commands, fields and answer keys of the protocol (`addon/shared/contract.py`).

Two tests protect them. `test_registration`, in `tests/test_blender.py`, compares what the extension registers with `tests/snapshots/registration.json`. `test_compat` opens `tests/fixtures/compat_0_17.blend`, a file saved before 1.0, and checks that it keeps its values and cooks again. The fixture was saved by a pre-release build, the oldest format the extension reads, and is never written again.

When a change to what is registered is meant, rewrite the snapshot and read its diff before committing it:

```powershell
$env:BRIDGE_UPDATE_SNAPSHOT = "1"; $env:BRIDGE_TEST = "registration"
& $blender --background --factory-startup --python tests/test_blender.py
Remove-Item Env:BRIDGE_UPDATE_SNAPSHOT, Env:BRIDGE_TEST
git diff tests/snapshots/registration.json
```

## Protocol version

`PROTOCOL_VERSION`, in `addon/shared/contract.py`, is checked at `hello`: a server of another version answers an error and exits, and the client says to reinstall the extension. Raise it whenever one side would misread the other: a field, key or command removed or renamed, a value that changes meaning or format, a new field the other side must read. Both sides come from the same folder, so they only differ when the extension was updated while Blender kept its old modules. `test_contract` freezes the version and the names: update it with the change.

## Style

- Lint with `uvx ruff check .` (configured in `pyproject.toml`). There is no automatic formatter: keep the style of the file you edit.
- Everything in the repository is in English: code, comments, documentation, messages.
- Docstrings and comments say why, in short sentences.
- Commit messages are one line, in the imperative: `Add a stream for closed curves`.
- When a change moves or renames something the documents name, `tests/unit/test_docs.py` tells you which line to fix.
