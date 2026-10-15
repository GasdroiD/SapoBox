# Upstream sources

`ps5gc/` contains selected files from Juma Sayeh's PS5 Game Compressor:
https://github.com/juma-sayeh/PS5-Game-Compressor
Commit: `5a040799eed7a8355ae9194608f353717b6634d5`.

Local adaptations: Linux statfs, source-image extent offsets, preservation of
empty directories. The Linux adapter calls the exFAT/PFS/PFSC writer directly;
it does not start a console payload. Compression uses system zlib.

**License.** The upstream tree has no LICENSE file. Its author gave written permission
to redistribute these files under GPL-3.0-or-later with attribution
(https://github.com/juma-sayeh/PS5-Game-Compressor/issues/72, 2026-10-13). These files
keep their upstream attribution.

AMPR index implementation is upstream's port of drakmor's build_ampr_index.py.
Catalog: https://github.com/Ephellon/game-store-catalog (fetched at runtime).

`lz4gc/` contains the host-compatible AMPRPAK4/AMPRDAT3 packer from
https://github.com/theghostonline/PS5-LZ4-Compression at commit
`cdf49eb90031e133dcb2e020816c466f2e2f89c5` (sources in src/ and tests/host/).

**License.** The packer is a port of drakmor's GPL-3.0 `ampr_pack.py`. Its author
confirmed that the files taken from that commit are GPL-3.0-or-later
(https://github.com/theghostonline/PS5-LZ4-Compression/issues/10, 2026-10-14). Code in that
repository that came from PS5 Game Compressor is not his to relicense; see above.

`lz4/` contains LZ4 1.10.0 with its BSD-2-Clause license.
The packer deliberately does not embed the PackedStdio binary: an operator
must supply the exact pinned compatible runtime locally (see the main README).
The packer is built against system zlib rather than bundling the upstream zlib sources.
Review `lz4gc/THIRD_PARTY_NOTICES.md` for the upstream's own list of components.
