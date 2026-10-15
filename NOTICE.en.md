# License notices and credits

[Português](NOTICE.md)

The code in this project, except where noted below, is distributed under the
**GNU GPL v3 or later** (see [`LICENSE`](LICENSE)). Third-party components keep the licenses and
rights of their authors.

## Code included in the repository

| Component | Location | Author(s) | License |
| --- | --- | --- | --- |
| LZ4 1.10.0 (`lz4.c`, `lz4.h`, `lz4hc.c`, `lz4hc.h`) | `vendor/converter/lz4/` | Yann Collet | BSD-2-Clause. Full notice in `vendor/converter/lz4/LICENSE`. |
| FF-PFSC engine (PFS/PFSC writer, validation, AMPR index) | `vendor/converter/ps5gc/` | Juma Sayeh (PS5 Game Compressor), with Linux adaptations | GPL-3.0-or-later, by written permission of the author (see below) |
| AMPR LZ4 packer (C port of `ampr_pack.py`) | `vendor/converter/lz4gc/` | Drakmor (`ampr_pack.py`); port by theghostonline (PS5-LZ4-Compression) | GPL-3.0-or-later, confirmed by the author (see below) |
| Upstream notes of the packer | `vendor/converter/lz4gc/THIRD_PARTY_NOTICES.md` | Copy of upstream | Describes the whole upstream tree. Not every component it mentions is in this repository. |
| 7-Zip 25.00 and the RAR decoder (`7z`, `7z.so`, `Codecs/Rar.so`) | `vendor/7zip/` | Igor Pavlov and contributors, packaged by Debian (`7zip` and `7zip-rar`) | LGPL-2.1 and BSD, with the unRAR restriction. See `vendor/7zip/copyright` and `vendor/7zip/unRarLicense.txt`. |
| Own code (interface, engine, tests, C bridge, packaging) | `sapobox/`, `native/`, `tests/`, `packaging/`, `extensao/` | SapoBox contributors | GPL-3.0-or-later |

### Permissions from the original authors

Neither source project had a license file. The authors authorized redistribution in writing, in public issues:

- **Juma Sayeh** (PS5 Game Compressor, commit `5a040799eed7a8355ae9194608f353717b6634d5`):
  allowed use under GPL-3.0 in
  [juma-sayeh/PS5-Game-Compressor#72](https://github.com/juma-sayeh/PS5-Game-Compressor/issues/72)
  (2026-10-13) and said the license file would be added upstream.
- **theghostonline** (PS5-LZ4-Compression, commit `cdf49eb90031e133dcb2e020816c466f2e2f89c5`):
  added a `LICENSE` (GPL v3) and `GPL-3.0-or-later` headers to the packer files, and confirmed this covers the
  code taken from that commit, in
  [theghostonline/PS5-LZ4-Compression#10](https://github.com/theghostonline/PS5-LZ4-Compression/issues/10)
  (2026-10-14). He noted that anything from PS5 Game Compressor is not his to relicense; that part is
  covered by Juma Sayeh's permission above.

### APR runtime

The **PackedStdio `libSceAmpr.sprx` 0.4.2.1** runtime is not part of the code. It comes from
[drakmor/ampr_emu](https://github.com/drakmor/ampr_emu), licensed GPL-3.0 according to upstream.
The converter only accepts the file with SHA-256
`69e6c4d5e4f5fb83c9e01815db5861c4c75734acbf4595cafa50d4c218116d1a`. The `runtime/` folder is in
`.gitignore`. The `.deb` published on Releases includes this file, unmodified (SHA-256 identical to the
upstream 0.4.2.1 release asset). The corresponding source code is at
[drakmor/ampr_emu, tag 0.4.2.1](https://github.com/drakmor/ampr_emu/releases/tag/0.4.2.1), and the GPL-3.0
license ships with the package at `/usr/share/doc/sapobox/LICENSE`.

## Credits

This project builds on the work of the people and projects below.

- **Drakmor**: ampr_emu (PackedStdio runtime), ShadowMountPlus, `build_ampr_index.py`
  and `ampr_pack.py`, whose AMPRPAK4/AMPRDAT3 format the LZ4 packer reproduces.
- **Juma Sayeh**: [PS5 Game Compressor](https://github.com/juma-sayeh/PS5-Game-Compressor),
  basis of the FF-PFSC engine.
- **theghostonline**: [PS5-LZ4-Compression](https://github.com/theghostonline/PS5-LZ4-Compression),
  basis of the LZ4 pipeline.
- **Nazky, Deckerr97, Pippo, Drakmor and williamtcastro**: [Lazy_AMPR](https://github.com/Nazky/Lazy_AMPR),
  packing workflow and automatic profile.
- **PSBrew**: [MkPFS](https://github.com/PSBrew/MkPFS), basis of the PFS image work.
- **Ephellon**: [game-store-catalog](https://github.com/Ephellon/game-store-catalog),
  queried at run time for game titles. The data belongs to its authors.
