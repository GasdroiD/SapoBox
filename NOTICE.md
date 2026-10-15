# Avisos de licença e créditos

[English](NOTICE.en.md)

O código deste projeto, exceto onde indicado abaixo, é distribuído sob a
**GNU GPL v3 ou posterior** (veja [`LICENSE`](LICENSE)). Os componentes de terceiros mantêm as
licenças e os direitos de seus autores.

## Código incluído no repositório

| Componente | Onde está | Autor(es) | Licença |
| --- | --- | --- | --- |
| LZ4 1.10.0 (`lz4.c`, `lz4.h`, `lz4hc.c`, `lz4hc.h`) | `vendor/converter/lz4/` | Yann Collet | BSD-2-Clause. Aviso completo em `vendor/converter/lz4/LICENSE`. |
| Motor FF-PFSC (writer PFS/PFSC, validação, índice AMPR) | `vendor/converter/ps5gc/` | Juma Sayeh (PS5 Game Compressor), com adaptações para Linux | GPL-3.0-or-later, por autorização escrita do autor (veja abaixo) |
| Packer LZ4 AMPR (porte em C de `ampr_pack.py`) | `vendor/converter/lz4gc/` | Drakmor (`ampr_pack.py`); porte de theghostonline (PS5-LZ4-Compression) | GPL-3.0-or-later, confirmada pelo autor (veja abaixo) |
| Notas do upstream do packer | `vendor/converter/lz4gc/THIRD_PARTY_NOTICES.md` | Cópia do upstream | Descreve a árvore completa do upstream. Nem todos os componentes citados estão neste repositório. |
| 7-Zip 25.00 e decodificador RAR (`7z`, `7z.so`, `Codecs/Rar.so`) | `vendor/7zip/` | Igor Pavlov e colaboradores, empacotado pelo Debian (`7zip` e `7zip-rar`) | LGPL-2.1 e BSD, com a restrição unRAR. Veja `vendor/7zip/copyright` e `vendor/7zip/unRarLicense.txt`. |
| Código próprio (interface, motor, testes, ponte C, empacotamento) | `sapobox/`, `native/`, `tests/`, `packaging/`, `extensao/` | Colaboradores do SapoBox | GPL-3.0-or-later |

### Permissões dos autores originais

Os dois projetos de origem não tinham arquivo de licença. Os autores autorizaram a
redistribuição por escrito, em issues públicas:

- **Juma Sayeh** (PS5 Game Compressor, commit `5a040799eed7a8355ae9194608f353717b6634d5`):
  autorizou o uso sob GPL-3.0 em
  [juma-sayeh/PS5-Game-Compressor#72](https://github.com/juma-sayeh/PS5-Game-Compressor/issues/72)
  (2026-10-13) e informou que adicionaria o arquivo de licença ao upstream.
- **theghostonline** (PS5-LZ4-Compression, commit `cdf49eb90031e133dcb2e020816c466f2e2f89c5`):
  adicionou `LICENSE` (GPL v3) e cabeçalhos `GPL-3.0-or-later` aos arquivos do packer, e confirmou que isso
  cobre o código tomado desse commit, em
  [theghostonline/PS5-LZ4-Compression#10](https://github.com/theghostonline/PS5-LZ4-Compression/issues/10)
  (2026-10-14). Ele avisou que o que veio do PS5 Game Compressor não é dele para relicenciar; esse
  trecho está coberto pela permissão de Juma Sayeh acima.

### Runtime APR

O runtime **PackedStdio `libSceAmpr.sprx` 0.4.2.1** não faz parte do código. Ele é de
[drakmor/ampr_emu](https://github.com/drakmor/ampr_emu), licenciado em GPL-3.0 segundo o upstream.
O conversor só aceita o arquivo com o SHA-256
`69e6c4d5e4f5fb83c9e01815db5861c4c75734acbf4595cafa50d4c218116d1a`. A pasta `runtime/` está no
`.gitignore`. O `.deb` publicado nas Releases inclui esse arquivo, sem alterações (SHA-256 igual ao do
asset da release 0.4.2.1 do upstream). O código-fonte correspondente está em
[drakmor/ampr_emu, tag 0.4.2.1](https://github.com/drakmor/ampr_emu/releases/tag/0.4.2.1), e a licença
GPL-3.0 acompanha o pacote em `/usr/share/doc/sapobox/LICENSE`.

## Créditos

Este projeto se baseia no trabalho das pessoas e projetos abaixo.

- **Drakmor**: ampr_emu (runtime PackedStdio), ShadowMountPlus, `build_ampr_index.py`
  e `ampr_pack.py`, cujo formato AMPRPAK4/AMPRDAT3 o packer LZ4 reproduz.
- **Juma Sayeh**: [PS5 Game Compressor](https://github.com/juma-sayeh/PS5-Game-Compressor),
  base do motor FF-PFSC.
- **theghostonline**: [PS5-LZ4-Compression](https://github.com/theghostonline/PS5-LZ4-Compression),
  base do pipeline LZ4.
- **Nazky, Deckerr97, Pippo, Drakmor e williamtcastro**: [Lazy_AMPR](https://github.com/Nazky/Lazy_AMPR),
  fluxo de empacotamento e perfil automático.
- **PSBrew**: [MkPFS](https://github.com/PSBrew/MkPFS), base do trabalho com imagens PFS.
- **Ephellon**: [game-store-catalog](https://github.com/Ephellon/game-store-catalog),
  consultado em tempo de execução para os títulos dos jogos. Os dados pertencem aos seus autores.
