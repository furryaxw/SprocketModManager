# Submit a Mod

[中文](CONTRIBUTING.md) | **English**

The Registry accepts only public, auditable, open-source Sprocket mods. You do not
need to modify an existing Release or copy binaries into this repository.

1. Fill in the basic information in the "Submit mod" dialog on the Pages site and
   generate `sprocket-mod.json`.
2. Use the page to open GitHub's new-file flow, keeping the path as
   `mods/<package-id>/sprocket-mod.json`.
3. Open a Pull Request.
4. Wait for Registry CI and maintainer review.

`display_name` must contain at least one language. `description` may be omitted
entirely, or may contain any languages independently of the display name. Language
keys use open-ended tags such as `en`, `zh-Hans`, or `pt-BR`.

CI verifies that:

- the metadata matches `schemas/sprocket-mod.schema.json` and contains no version
  number or download URL;
- the GitHub repository is public, not archived, and has a GitHub-recognized SPDX
  open-source license;
- the repository contains `LICENSE`/`COPYING` and actual source files;
- at least one non-draft Release has a tag that can be parsed as SemVer;
- at least one Release asset matches the metadata include/exclude rules;
- every dependency and recommendation is registered, and the dependency graph is acyclic;
- installation overrides stay within `Mods`, `Plugins`, `UserLibs`, or `UserData`.

Validation never executes code from a submitted repository. Public source alone
does not prove that a Release binary was built from that source. Reproducible builds
and GitHub Artifact Attestations are treated as separate trust indicators.
