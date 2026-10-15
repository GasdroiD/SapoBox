// Testes das funções puras. Rode com Node.js 18+:  node --test extensao/test_lista.js
const { test } = require("node:test");
const assert = require("node:assert/strict");
const L = require("./lista.js");

test("aceita só http e https, sem usuário e senha na URL", () => {
  assert.equal(L.normalizarLink(" https://exemplo.com/a.rar "), "https://exemplo.com/a.rar");
  for (const ruim of ["ftp://x/y", "javascript:alert(1)", "blob:https://x/1", "https://u:s@exemplo.com/a", "", null]) {
    assert.equal(L.normalizarLink(ruim), null, String(ruim));
  }
});

test("não repete um link já capturado", () => {
  const primeiro = L.adicionarLink([], "https://exemplo.com/a.rar");
  assert.equal(primeiro.adicionado, true);
  const repetido = L.adicionarLink(primeiro.lista, "https://exemplo.com/a.rar");
  assert.equal(repetido.adicionado, false);
  assert.equal(repetido.lista.length, 1);
});

test("guarda no máximo o limite, descartando os mais antigos", () => {
  let lista = [];
  for (let i = 0; i < L.MAX_LINKS + 5; i++) lista = L.adicionarLink(lista, `https://exemplo.com/${i}.rar`).lista;
  assert.equal(lista.length, L.MAX_LINKS);
  assert.equal(lista[0].url, "https://exemplo.com/5.rar");
});

test("texto da lista tem um link por linha e quebra no fim", () => {
  let lista = L.adicionarLink([], "https://a.com/1.rar").lista;
  lista = L.adicionarLink(lista, "https://a.com/2.rar").lista;
  assert.equal(L.textoDaLista(lista), "https://a.com/1.rar\nhttps://a.com/2.rar\n");
  assert.equal(L.textoDaLista([]), "");
});
