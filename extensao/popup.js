const $ = (id) => document.getElementById(id);
let estado = { capturando: false, lista: [] };

async function falar(mensagem) {
  const resposta = await chrome.runtime.sendMessage(mensagem);
  if (!resposta || !resposta.ok) throw new Error((resposta && resposta.erro) || "Não consegui falar com a extensão.");
  return resposta.dados;
}

function mostrarErro(texto) { $("erro").textContent = texto || ""; }

function desenhar() {
  $("capturar").checked = estado.capturando;
  $("estado-captura").textContent = estado.capturando ? "Captura ligada" : "Captura desligada";
  const total = estado.lista.length;
  $("contagem").textContent = total === 1 ? "1 link" : `${total} links`;
  const lista = $("lista");
  lista.replaceChildren();
  if (!total) {
    const vazio = document.createElement("li");
    vazio.textContent = "Nenhum link ainda.";
    lista.appendChild(vazio);
  }
  estado.lista.slice(-200).forEach((item, indice) => {
    const li = document.createElement("li");
    const url = document.createElement("span");
    url.className = "url";
    url.textContent = item.url;
    url.title = item.url;
    const remover = document.createElement("button");
    remover.type = "button";
    remover.className = "pequeno";
    remover.textContent = "×";
    remover.setAttribute("aria-label", "Remover este link");
    const posicao = Math.max(0, total - 200) + indice;
    remover.onclick = () => atualizar({ acao: "remover", indice: posicao });
    li.append(url, remover);
    lista.appendChild(li);
  });
}

async function atualizar(mensagem) {
  try {
    estado = await falar(mensagem);
    mostrarErro("");
    desenhar();
  } catch (erro) {
    mostrarErro(erro.message);
  }
}

$("capturar").addEventListener("change", (ev) => atualizar({ acao: "capturando", valor: ev.target.checked }));
// Não usa confirm(): o diálogo nativo do navegador fica cortado pela janela pequena da extensão.
const painelLimpeza = $("painel-limpeza");
function mostrarPainelLimpeza(visivel) {
  painelLimpeza.hidden = !visivel;
  $(visivel ? "cancelar-limpeza" : "limpar").focus();
}
$("limpar").addEventListener("click", () => {
  if (!estado.lista.length) return mostrarErro("A lista já está vazia.");
  mostrarErro("");
  mostrarPainelLimpeza(true);
});
$("cancelar-limpeza").addEventListener("click", () => mostrarPainelLimpeza(false));
$("confirmar-limpeza").addEventListener("click", () => {
  mostrarPainelLimpeza(false);
  atualizar({ acao: "limpar" });
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !painelLimpeza.hidden) mostrarPainelLimpeza(false);
});
$("copiar").addEventListener("click", async () => {
  if (!estado.lista.length) return mostrarErro("A lista está vazia.");
  try {
    await navigator.clipboard.writeText(estado.lista.map((i) => i.url).join("\n") + "\n");
    mostrarErro("Lista copiada. Cole no SapoBox.");
  } catch (erro) {
    mostrarErro("Não consegui copiar. Use Baixar .txt.");
  }
});
$("baixar").addEventListener("click", () => {
  if (!estado.lista.length) return mostrarErro("A lista está vazia.");
  const blob = new Blob([estado.lista.map((i) => i.url).join("\n") + "\n"], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "links.txt";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

atualizar({ acao: "estado" });
