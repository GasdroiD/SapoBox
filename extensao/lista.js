// Funções puras da lista de links. Não usam o navegador, por isso podem ser testadas.
(function (raiz) {
  "use strict";
  const MAX_LINKS = 2000;
  const PROTOCOLOS = ["http:", "https:"];

  function normalizarLink(valor) {
    if (typeof valor !== "string") return null;
    const texto = valor.trim();
    if (!texto || texto.length > 8192) return null;
    let url;
    try {
      url = new URL(texto);
    } catch (erro) {
      return null;
    }
    if (!PROTOCOLOS.includes(url.protocol) || url.username || url.password) return null;
    return url.href;
  }

  function adicionarLink(lista, valor) {
    const url = normalizarLink(valor);
    if (!url) return { lista, adicionado: false, motivo: "Link inválido ou sem http/https." };
    if (lista.some((item) => item.url === url)) return { lista, adicionado: false, motivo: "Este link já está na lista." };
    const nova = [...lista, { url, quando: new Date().toISOString() }].slice(-MAX_LINKS);
    return { lista: nova, adicionado: true, motivo: "" };
  }

  function textoDaLista(lista) {
    return lista.map((item) => item.url).join("\n") + (lista.length ? "\n" : "");
  }

  const api = { MAX_LINKS, normalizarLink, adicionarLink, textoDaLista };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  raiz.PS5Lista = api;
})(typeof globalThis !== "undefined" ? globalThis : self);
