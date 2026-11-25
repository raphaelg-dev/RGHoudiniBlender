# RG Houdini Bridge: protocol

The wire format between Blender and hython, and what each command carries. Part of the developer documentation: start with [DEVELOPMENT.md](../../DEVELOPMENT.md).

## Protocol

One message is 4 bytes (JSON header length, little-endian), the JSON header, then the binary blocks listed in `header["blocks"]` as `[name, numpy dtype, byte count]`. Blocks are sent from their own memory (`memoryview`) and received with `recv_into` straight into the numpy buffer, so received arrays are writable.

`shared/contract.py` is the reference for what a header holds, and both sides import it. For each command, `REQUESTS` lists the fields of its request, each with its default or `REQUIRED` and one line of specification, and `ANSWERS` lists the keys of its answer.

The commands, the input states (`FULL`, `POSITIONS`, `UNCHANGED`, `NONE`, `CHAIN`), the output modes (`NATIVE`, `MESH`), the native streams (`STREAMS`: `INSTANCES`, `CURVES`, `POINTS`, `VOLUME`, which are keys of the cook answer and are stored in `.blend` files), the parameter kinds (`PARM_KINDS`) and the fields of a parameter description (`PARM_FIELDS`), and the values both sides need (`MAX_INPUTS`, `SPHERE_FREQUENCY` and `SPHERE_FREQUENCY_RANGE`, `INPUT_PREFIX`, `PORT_LINE`, `TOKEN_VARIABLE`) are constants there, and nowhere else.

The Blender side builds every request with `contract.request(command, **fields)`, which refuses a field the command does not declare and a missing required one. The server reads every field with `contract.field(header, name)`, which gives the declared default (a copy when it is a list or a dict), and answers `error` to a request that carries an undeclared field, naming it.

`tests/unit/test_contract.py` checks that no command, state or mode is written by hand outside the contract, that `houdini/` never reads a header directly, and that the table below names what the contract declares, and nothing else.

| Command | Request | Answer |
|---|---|---|
| `hello` | `token`, `version` | `ok`, `houdini`, `version` |
| `definitions` | `path` | `definitions` |
| `cook` | `instance`, `path`, `type`, `inputs`, `parms`, `buttons`, `input_groups`, `input_attributes`, `chains`, `input_volumes`, `extras`, `frame`, `fps`, `output`, `volume_dir`, `sphere_frequency`, `resend` | `label`, `version`, `inputs`, `parms`, `errors`, `warnings`, `timings`, `time_dependent`, `renumbered`, `materials`, `attributes`, `groups`, `instances`, `curves`, `points`, `volume`, `missing_inputs`, `missing_chains` |
| `catalog` | `folders`, `packages`, `icon_dir`, `native`, `all_versions` | `tools`, `duration` |
| `write_volume` | `source`, `destination`, `matrix` | `ok` |
| `logo` | `path` | `ok` |
| `delete` | `instance` | `ok` |
| `clear` | | `ok` |
| `ping` | | `ok` |

Any command may answer `error` instead: an exception (a faulty HDA cannot bring the server down), a refused request, or an unknown command. `protocol.send` raises before the first byte leaves when a message cannot go on the wire (a header JSON cannot write, a block of Python objects), so the server can answer `error` in its place. On the client, any exception between sending and receiving stops hython (`hython.Connection.request`): the stream is no longer in step, and the next request would read blocks as a header.

**Definitions**: each entry has a type, a label, a category, whether it is supported (a SOP) and its input count, at most `MAX_INPUTS`.

**Cook request**: `inputs` holds one state per input: full (the whole mesh is attached, or a Volume object through `input_volumes`), positions (P, uv and attribute values on the topology already received), unchanged, none, or chain (the Houdini modifier above, through `chains`). The blocks of input `i` are `in<i>_P`, `in<i>_counts`, `in<i>_indices`, `in<i>_uv`, `in<i>_g<n>` (groups) and `in<i>_a<n>` (attributes). `output` is native or mesh (the default).

`sphere_frequency` is the *Sphere Detail* preference. A required field has no default (`instance`, `type`); the others take theirs from the contract when they are missing.

**Cook answer**: blocks `P`, `counts`, `indices`, `uv`, `N`, `Cd_point` or `Cd_corner`, `material`, `attr<n>`, `group<n>`. In native mode it also carries `instances`, `curves`, `points` and `volume` (`contract.STREAMS`), with their blocks.

`renumbered` says whether the mesh is numbered differently from the HDA's own output, which a modifier chained below reads (`Instance.renumbered`: the counts of points and primitives differ, or not every primitive is a polygon); Blender keeps it per modifier in `CookState` (`cook.sent.is_renumbered`), for the viewport group selection.

When the server lost inputs (a restart, a reloaded HDA) it answers `missing_inputs` and `missing_chains` instead of a result, and the Blender side sends them again in full.

**Parameter descriptions**: the `parms` of a cook answer hold one description per parameter. Its `kind` is one of `PARM_KINDS`: `float`, `int`, `toggle`, `string`, `menu`, `button`, `ramp`, also stored in `.blend` files, so never renamed.

`PARM_FIELDS` lists the fields, which kinds send each one and what it holds:

- every kind sends `name`, `label`, `folder`, `folder_types` (one `[type, tab set, name]` per level of `folder`, the type one of `FOLDER_TYPES`, the name the folder's Houdini name), `help`, `size`, `hidden`, `disabled`, `kind` and `expression` (Houdini drives a component with keyframes or an expression, a channel reference included);
- all but `button` send `value`;
- `float` and `int` send `min`, `max`, `min_strict` and `max_strict`; `float` sends `is_color`; the count of a multiparm is an `int` with `multiparm`;
- a `string` sends `is_file`, `editor` for a code editor (the language of its `editorlang` tag, lowercase, `text` when it names none; see [Code parameters](parameters.md#code-parameters)), or `suggestions` and `toggle` for a group field, with `group_input`, `group_types` and `group_type_parm`, what its viewport selection takes (see [Selecting a group in the viewport](features.md#selecting-a-group-in-the-viewport));
- a `menu` sends `menu`, and a `ramp` sends `ramp`.

Blender sends the values back in `parms` in the same format, but for those with `expression` that did not change in Blender since the last cook, which Houdini goes on evaluating (`cook/request._values_sent`; none of them at the first cook of a session); a value sent replaces the expression of each component it changes (`parms._set`). It sends the names of the buttons to press in `buttons`.

**Startup and version**: the server prints `HOUDINI_BRIDGE_PORT <n>` on stdout once it listens, and checks the random token passed in `HOUDINI_BRIDGE_TOKEN` in `hello`. `hello` also carries `PROTOCOL_VERSION`, and the server answers with its own.

When the token is wrong, or the version differs or is missing, the server answers `error` with its version and exits; the client raises a `ServerError` that names both versions and says to reinstall the extension (the two sides come from the same folder, so they only differ when the extension was updated while Blender kept the old modules), and stops the hython it started. `PROTOCOL_VERSION` changes whenever one side would misread the other.
