# Helper Windows sem Python instalado

O pacote local `bees-host-windows.zip` contém o helper de **diagnóstico somente leitura**, seu runtime e o atalho gráfico. Não cria VM nem concede execução ou acesso pessoal. É um checkpoint de BEES-028; instalação completa, recuperação guiada e outras plataformas continuam pendentes.

## Usar

Com a aplicação Bees já aberta pelo Docker e o acesso configurado, extraia o ZIP inteiro em uma pasta local e abra **Conectar host Bees.vbs**. Compare o código mostrado pelo launcher com o da aba **Computador**, confirme o diagnóstico e acompanhe o estado. Para encerrar a autorização, revogue o vínculo na interface. Python, `.venv` e terminal não são necessários para esse fluxo.

O pacote usa Docker Desktop com containers Linux e a instalação local padrão `bees`, na porta8080. Não inicia nem compila a aplicação Docker sozinho. Configurações de operador com projeto/porta diferentes usam os parâmetros do launcher; não alterar destinos automaticamente. O pacote não instala serviço, autostart ou privilégios de administrador.

O estado privado fica em `data/host-links/<projeto>-<porta>` junto ao launcher, protegido por DPAPI CurrentUser e ACL exclusiva. Mantenha essa pasta durante atualização do runtime e pare o helper antes de substituir seus arquivos. Não copie o estado para outra conta/instalação como restauração. O estado de um checkout permanece na pasta `data` desse checkout; o build não o incorpora ou migra. Reabrir o mesmo launcher reutiliza a identidade e a trava impede duas instâncias. Convite, confirmação, expiração e revogação estão em [computadores](environments.md#vínculo-de-diagnóstico-do-host).

O launcher prefere o runtime empacotado em caminho fixo. Somente um checkout sem pasta de runtime pode usar `.venv` como alternativa de desenvolvimento. Runtime presente mas incompleto não provoca fallback silencioso. A verificação de ACL usa uma opção fechada do helper, sem avaliação de código Python.

## Construir e conferir — contribuidores

O build deve ocorrer no Windows x64, com Python3.14 e dependências do próprio checkout:

```powershell
uv sync --locked --group packaging
.venv\Scripts\python.exe scripts/build-host.py
.venv\Scripts\python.exe scripts/build-host.py --verify dist/host-windows/bees-host-windows
```

PyInstaller6.22.3 e hooks são fixados no `uv.lock`, em grupo separado das dependências do serviço. A especificação versionada gera `onedir`, sem UPX ou elevação. O [PyInstaller não faz compilação cruzada](https://pyinstaller.org/en/stable/); suporte Linux/macOS não é inferido deste artefato.

O build usa staging temporário sob `dist/host-windows`, conserva o build anterior e não apaga estado de execução. Artefatos e logs são locais/ignorados. O ZIP contém somente runtime, launcher, Compose para localizar a instalação existente, documentação, inventários e avisos de licença. Não inclui banco, cofre, convites, `.env`, fontes/testes da API ou dados de usuário.

Se a pasta de saída já contiver `data` de uso, o build recusa substituí-la. Atualizações de uma pasta em uso devem preservar esse estado e substituir somente o runtime, com helper parado; recuperação/atualização gráfica automática ainda não foi implementada.

`module-inventory.json` lista os módulos Python incorporados. A análise recusa API/core/worker/APSW/FastAPI/Uvicorn e ferramentas de teste/build no runtime. `package-info.json` registra versões, dependências efetivamente identificadas e hashes das fontes do helper. `SHA256SUMS.json` registra SHA256 e tamanho de todos os arquivos do pacote antes do uso; a conferência recusa arquivos extras/ausentes, hashes divergentes e links/reparse points. Confira antes de iniciar, pois o estado privado criado depois não pertence ao inventário de distribuição.

Esses hashes detectam cópia incompleta ou alteração em relação ao manifesto; não autenticam origem quando manifesto e binários são alterados juntos. O build local ainda não é assinado nem publicado. A licença do Bees e a revisão de avisos de componentes nativos permanecem requisitos de publicação; licenças disponíveis nos wheels e no Python instalado são preservadas no pacote. Binários graváveis pelo próprio usuário não constituem isolamento contra código dessa conta.

A sondagem Windows usa o PowerShell absoluto do sistema, diretório e ambiente de sistema, sem módulos/PATH do usuário. Antes do subprocesso, o helper congelado restaura temporariamente a busca de DLLs do sistema e depois repõe a configuração anterior, conforme as [orientações oficiais do PyInstaller](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application). Isso não habilita Hyper-V.

## Validar o executável

```powershell
$env:BEES_HOST_EXECUTABLE = (Resolve-Path dist/host-windows/bees-host-windows/runtime/bees-host/bees-host.exe).Path
.venv\Scripts\python.exe -m pytest apps/host/tests
Remove-Item Env:BEES_HOST_EXECUTABLE
```

Os testes opt-in copiam o runtime para pasta temporária fora do checkout com espaços/acentos e removem Python do PATH. Usam API/banco/identidade descartáveis e HTTP loopback real. Cobrem diagnóstico nativo, DPAPI/ACL, perda de resposta após aceite, reconciliação sem repetir relatório, reinício, revogação, convite expirado e trava de múltiplas instâncias. Não configuram a instalação padrão, habilitam hipervisor ou afirmam VM/isolamento.

### Evidência local — 06/10/2026

Windows: suíte completa com o executável final, 865 testes aprovados e 27 skips ambientais; após acrescentar a guarda de preservação, os 8 testes afetados do pacote passaram. Helper: 77 aprovados e 3 skips, incluindo os 6 cenários opt-in com executável real. Linux: 469 aprovados e 13 skips. Frontend: 332 testes, lint, tipos e build aprovados.

O ZIP foi extraído fora do checkout, em caminho com espaços/acentos e sem `.venv`. O launcher PowerShell 5.1 conectou ao Compose descartável; a interface confirmou o código, recebeu o diagnóstico nativo e atualizou a preparação da instalação ao atualizar os hosts, sem recarregar o painel. Reinício do helper e dos containers preservou a identidade; a revogação encerrou o helper. O invólucro VBS não foi acionado nesta validação. Evidência visual local em `data/validation/bees-host-package-desktop.png`, ignorada pelo Git.

O pacote conferido contém 115 arquivos, 501 módulos e aproximadamente 40 MB descompactados. SHA256 do ZIP: `8b42085171340b14b7eddbb06bddfe6faafb0d4361377ffd29aa0ae69e53ddec`. A instalação padrão permaneceu no schema6, com identidade e cofre preservados.
