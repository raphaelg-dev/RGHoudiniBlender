# Security

Opening a `.blend` file whose Houdini modifiers are not locked starts Houdini (`hython`) and loads the HDA files the file names. An HDA can run Python, so a file can bring code that runs on your machine, as a file's own scripts can.

- The extension follows Blender's *Auto Run Python Scripts* preference and its *Excluded Paths*. A file Blender would not let run its scripts cooks nothing, and starts no Houdini, until you click *Allow Execution* or act on one of its Houdini modifiers. See [Opening a file](docs/GUIDE.md#opening-a-file).
- Blender 5.2 does not tell an add-on that a file was opened as a *Trusted Source*: with *Auto Run Python Scripts* off, such a file asks too.
- The server listens on 127.0.0.1 only, on a port chosen at each start, and answers only a client that sends the random token it was started with.
- Allow only the files you trust, as you would for their scripts.

To report a vulnerability, use GitHub's *Report a vulnerability* button in the *Security* tab of the repository, or open an issue that does not describe the flaw and ask for a private contact. This is a personal project: there is no fixed delay for an answer.
