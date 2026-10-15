// Captura os downloads do navegador e guarda os links numa lista.
importScripts("lista.js");
const L = self.PS5Lista;
const CHAVE = "estado";

async function ler() {
  const { [CHAVE]: estado } = await chrome.storage.local.get(CHAVE);
  return estado && Array.isArray(estado.lista) ? estado : { capturando: false, lista: [] };
}

async function gravar(estado) {
  await chrome.storage.local.set({ [CHAVE]: estado });
  atualizarBadge(estado);
}

function atualizarBadge(estado) {
  chrome.action.setBadgeText({ text: estado.lista.length ? String(estado.lista.length) : "" });
  chrome.action.setBadgeBackgroundColor({ color: estado.capturando ? "#1f5fd6" : "#5b6b82" });
}

async function adicionar(valor) {
  const estado = await ler();
  const resultado = L.adicionarLink(estado.lista, valor);
  if (resultado.adicionado) {
    estado.lista = resultado.lista;
    await gravar(estado);
  }
  return resultado;
}

// Com a captura ligada, o download é cancelado no navegador e o link vai para a lista.
// Só se o link for http/https; o restante segue como o navegador decidir.
chrome.downloads.onCreated.addListener(async (item) => {
  const estado = await ler();
  if (!estado.capturando) return;
  const url = item.finalUrl || item.url;
  if (!L.normalizarLink(url)) return;
  await adicionar(url);
  try {
    await chrome.downloads.cancel(item.id);
    await chrome.downloads.erase({ id: item.id });
  } catch (erro) {
    // O download pode já ter terminado; a lista continua com o link.
  }
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "adicionar-link", title: "Adicionar à lista do SapoBox", contexts: ["link"] });
  });
  ler().then(atualizarBadge);
});

chrome.runtime.onStartup.addListener(() => {
  ler().then(atualizarBadge);
});

chrome.contextMenus.onClicked.addListener((info) => {
  if (info.menuItemId === "adicionar-link") adicionar(info.linkUrl);
});

chrome.runtime.onMessage.addListener((mensagem, remetente, responder) => {
  if (remetente.id !== chrome.runtime.id) return false;
  (async () => {
    const estado = await ler();
    switch (mensagem && mensagem.acao) {
      case "estado":
        return estado;
      case "capturando":
        estado.capturando = mensagem.valor === true;
        await gravar(estado);
        return estado;
      case "remover":
        if (!Number.isInteger(mensagem.indice)) throw new Error("Item inválido.");
        estado.lista = estado.lista.filter((_, i) => i !== mensagem.indice);
        await gravar(estado);
        return estado;
      case "limpar":
        estado.lista = [];
        await gravar(estado);
        return estado;
      default:
        throw new Error("Ação desconhecida.");
    }
  })().then(
    (dados) => responder({ ok: true, dados }),
    (erro) => responder({ ok: false, erro: erro.message })
  );
  return true;
});
