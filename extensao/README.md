# Extensão: capturar links

Extensão para Brave e Chrome que guarda os links de download numa lista. Ela é independente do SapoBox: não usa porta, token nem servidor.

## Instalar

1. Abra `brave://extensions` (ou `chrome://extensions`).
2. Ligue o **Modo do desenvolvedor**.
3. **Carregar sem compactação** e escolha esta pasta (`extensao`).

## Usar

- **Capturar downloads** (ícone da extensão): com a captura ligada, cada download do navegador é cancelado e o link vai para a lista.
- Clique com o botão direito num link e escolha **Adicionar à lista do SapoBox**.
- **Copiar lista** ou **Baixar .txt** (`links.txt`), e depois cole ou importe no SapoBox, na etapa Baixar.

Só links `http` e `https` são guardados, sem usuário e senha na URL. Links repetidos não entram de novo. A lista guarda até 2000 links.

## Testes

Funções puras em `lista.js`. Testes em `test_lista.js`, com Node.js 18+: `node --test extensao/test_lista.js`.
