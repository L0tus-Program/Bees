# Licença e revisão de dependências — BEES-026.4 (#93)

O mantenedor escolheu explicitamente **Apache-2.0 em 26/10/2026**, no aceite da issue #93. O texto integral está em [LICENSE](../LICENSE), com [NOTICE](../NOTICE). Os metadados Python e web declaram a mesma licença. Isso não altera a licença dos componentes de terceiros nem publica uma release.

O texto padrão foi obtido de `/usr/share/common-licenses/Apache-2.0`, distribuído pelo sistema Debian deste ambiente, sem modificar seus termos. A referência oficial é <https://www.apache.org/licenses/LICENSE-2.0>; a tentativa de obter o texto diretamente desse site neste ambiente retornou HTTP403.

Cada projeto Python inclui cópias de LICENSE/NOTICE em sua wheel e sdist. O verificador da wheel do executor recusa licença ausente, divergente ou sem a expressão SPDX. O bundle web inclui `third-party-notices.txt`, com textos integrais e créditos dos pacotes de produção/peer do lockfile, inclusive noVNC. As imagens conservam avisos do sistema e recebem os avisos do Bees em `/usr/share/doc/bees`.

Os avisos versionados usam finais de linha LF em todas as plataformas; as leituras
textuais declaram UTF-8. As verificações de artefato comparam bytes exatos, sem
normalizar o conteúdo para esconder divergência. Regressões de CI cobrem checkout
com CRLF e sistema cujo encoding padrão é Windows-1252; os testes de avisos e
pacotes também rodam na etapa portável antecipada do Windows.

Inventário levantado em 23/10/2026 a partir do `uv.lock` (ambiente `.venv`), `apps/web/package-lock.json` e das imagens `Dockerfile` e `runtime/computer/Dockerfile`. Repetir com o procedimento ao final antes de cada release.

## Dependências distribuídas com a aplicação

### Python (API, worker, core, executor)

| Licença | Pacotes |
| --- | --- |
| MIT / MIT-0 | annotated-doc, annotated-types, anyio, argon2-cffi(-bindings), attrs, cffi (MIT-0), fastapi, h11, jsonschema(-specifications), pydantic(-core), referencing, rpds-py, truststore, typing-inspection |
| BSD-2/3-Clause | click, httpcore, httpx, idna, pycparser, pypdf, starlette |
| Apache-2.0 (ou dupla com BSD) | cryptography (Apache-2.0 OR BSD-3), packaging (Apache-2.0 OR BSD-2), opentelemetry-api |
| MPL-2.0 | certifi (dados de CA; uso sem modificação) |
| PSF-2.0 | typing_extensions |
| OSI (zlib-like) | apsw |
| Executor (imagem do computador) | websockets (BSD-3), playwright (Apache-2.0), pypdf (BSD-3) |

Somente desenvolvimento: pytest, pluggy, iniconfig (MIT), pygments (BSD-2), ruff (MIT).

### Interface web (produção)

react, react-dom, scheduler, use-sync-external-store, i18next, react-i18next, html-parse-stringify, @babel/runtime (MIT); **@novnc/novnc 1.7.0 (MPL-2.0)**. O restante do lock (≈200 pacotes) é de build/teste: MIT, ISC, Apache-2.0, BSD, BlueOak-1.0.0, MPL-2.0 (ferramentas) e CC-BY-4.0 (dados `caniuse-lite`, não embarcados no bundle).

### Imagens Docker

| Imagem | Componentes relevantes | Licenças |
| --- | --- | --- |
| `bees` | Python 3.14 (PSF), Debian bookworm slim, bundle web | PSF, licenças Debian (mistas, incluindo GPL em utilitários do sistema) |
| `computer` | Ubuntu + XFCE, x11vnc, Xvfb, LibreOffice Writer/Calc, pandoc, Chromium (Playwright), iptables | GPL-2.0 (x11vnc, XFCE, pandoc, iptables), MPL-2.0 (LibreOffice), X11/MIT (Xvfb), BSD (Chromium) |
| `ollama` (opcional) | Ollama | MIT; pesos de modelo têm licenças próprias (ex.: Llama Community License, Apache-2.0 do Qwen) |

## Restrições encontradas

1. **noVNC (MPL-2.0)** entra no bundle web. MPL é copyleft por arquivo: modificações nos arquivos do noVNC precisam ser publicadas sob MPL; o restante do Bees pode ter outra licença. O Bees usa o pacote sem modificação. Manter o aviso de licença do noVNC nos créditos do bundle.
2. **Imagem do computador contém componentes GPL** (x11vnc, pandoc, XFCE, iptables, com versões/licenças próprias). São executáveis separados do Bees. Redistribuir uma imagem exige conferir os avisos e fornecer o código-fonte correspondente segundo as licenças aplicáveis; apenas citar um mirror não comprova essa obrigação. O procedimento e o responsável estão no [README da imagem](../runtime/computer/README.md). Nenhuma imagem pública é publicada por esta alteração.
3. **Modelos locais**: o Bees não distribui pesos. O download é feito pelo usuário via Ollama e cada modelo carrega sua própria licença (exibida na origem informada no diálogo de download).
4. **Nenhuma dependência AGPL/SSPL/BUSL** foi encontrada. Nenhuma dependência impede licenças permissivas ou copyleft para o Bees.
5. `bees-*` e o workspace declaram `license = "Apache-2.0"`; os pacotes incluem LICENSE/NOTICE. O frontend declara a mesma licença no manifest e lockfile.

## Alternativas consideradas antes da decisão

| Opção | Efeito | Observação |
| --- | --- | --- |
| Apache-2.0 | Permissiva com concessão explícita de patentes e aviso de marcas | Compatível com todas as dependências acima; comum em ferramentas de IA |
| MIT | Permissiva mínima | Sem cláusula de patentes |
| AGPL-3.0 | Copyleft inclusive para uso via rede | Garante que forks hospedados publiquem o código; reduz adoção corporativa |
| Proprietária / source-available | Mantém todos os direitos | Exige termos próprios para contribuidores |

A escolha confirmada é **Apache-2.0**. A publicação futura em pip/npm continua no backlog #108 e exige decisão própria. Antes de publicar binários ou imagens, o mantenedor responsável pela release deve conferir o inventário dos artefatos finais e os fontes/avisos nativos correspondentes. A validação local de wheels e a CI não são um parecer jurídico nem uma comprovação de um artefato Windows não construído nesta máquina.

## Procedimento de revisão (repetir por release)

```bash
# Python: licença por distribuição instalada no .venv
.venv/bin/python -c "import importlib.metadata as m; [print(d.metadata['Name'], d.version, d.metadata.get('License-Expression') or [c for c in (d.metadata.get_all('Classifier') or []) if c.startswith('License')]) for d in m.distributions()]"
# Web: licenças de produção registradas no lockfile
cd apps/web && node -e 'const l=require("./package-lock.json");for(const[k,v]of Object.entries(l.packages))if(k&&!v.dev)console.log(k.split("node_modules/").pop(),v.version,v.license)'
```

Pacotes do sistema das imagens: `docker run --rm <imagem> dpkg-query -W -f '${Package}\t${Version}\n'` e `/usr/share/doc/<pacote>/copyright`.
