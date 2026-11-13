# Conector da máquina pessoal

O conector deixa a abelha trabalhar no seu computador **somente** nas pastas e recursos que você conceder. Ele usa o mesmo executor do computador próprio ([ADR 0004](adr/0004-computer-runtime.md)).

## Instalar e parear

1. Na aba **Computador** da abelha, em **Máquina pessoal**, clique **Conectar máquina pessoal**. Um convite de uso único (15 minutos) aparece com o comando sugerido.
2. Na sua máquina (Windows, Linux ou macOS, Python 3.12+):

   ```sh
   pip install "./apps/executor[browser,documents]"   # a partir do checkout do Bees
   python -m playwright install chromium            # somente se for conceder navegador
   bees-executor personal --api http://127.0.0.1:8080 --invite=<convite> \
     --root Documentos=~/Documents --root Downloads=~/Downloads
   ```

   Cada `--root Alias=Caminho` declara uma pasta compartilhável. Nada fora delas fica acessível.

   O pacote `bees-executor` **não está publicado** em PyPI: não instale esse nome a partir de índices públicos (um pacote homônimo seria de terceiros). Use o checkout ou a wheel do artefato **bees-executor-wheel** do CI, conferindo o `SHA256SUMS` (`sha256sum -c SHA256SUMS`).
3. De volta à interface, o conector aparece **conectado**. Para cada abelha, marque as pastas concedidas e, se quiser, **Escrita**, **Terminal** e **Navegador**; clique **Salvar concessão**.
4. Instale/conceda o pacote **Máquina pessoal (conector)** (`bees.personal`) em **Ferramentas**.

A credencial fica em `executor-state/credential.json` (permissão restrita). Reiniciar o conector reconecta sem novo convite.

Somente uma CLI pode usar cada diretório `--state`, inclusive enquanto aguarda
convite ou reconexão. `executor_state_in_use` indica outra instância em execução:
encerre-a antes de reiniciar. Não apague `executor.lock`; ele permanece no diretório,
mas a trava é liberada pelo sistema após saída ou crash. A segunda instância não
abre o journal, consome convites nem altera a credencial. Se a trava não puder ser
aberta, `executor_state_unavailable` orienta conferir o acesso ao estado privado.
Use filesystem local; não há exclusividade entre diretórios de estado distintos.

O journal existente é validado antes de recuperar comandos interrompidos.
`executor_journal_invalid` encerra o conector antes de parear ou conectar quando
o arquivo está vazio, ilegível, corrompido ou possui schema/registros incompatíveis.
A primeira abertura grava `journal.required` no estado privado antes de criar o
journal. A ausência posterior do histórico bloqueia a inicialização enquanto esse
marcador existir, mesmo após a revogação remover a credencial ou durante a espera
pelo primeiro convite. Credencial salva sem journal também bloqueia, inclusive em
estado legado sem marcador. Nenhum histórico novo, pasta, perfil ou ferramenta é
preparado nesses casos.

Preserve credencial, convite, marcador e journal; restaure o journal original de
backup confiável após parar os processos. Não apague o histórico nem o marcador
para reparear. A primeira instalação sem credencial nem marcador ainda cria o
journal antes de trocar o convite; um journal legado válido recebe o marcador.
A restauração do histórico não reativa credencial revogada. Perda anterior à
atualização sem marcador/credencial, perda conjunta de journal/marcador, exclusão
de todo o estado, rollback válido e exclusão durante conexão aberta ficam fora
da detecção. O computador próprio usa o mesmo mecanismo por home privada; veja
[recuperação dos journals](recovery.md#journal-dos-computadores-no-runtime-container).

O estado é vinculado à URL da API usada no pareamento. Se outro `--api` for
informado no reinício, `executor_api_mismatch` encerra o executor antes de enviar
a credencial. O arquivo é preservado; reinicie com a URL original. Uma barra
final é equivalente, mas outras diferenças de URL são recusadas. Para outra
instalação, use outro `--state` e faça um pareamento próprio. Estado sem vínculo
válido também é bloqueado: não edite nem apague a credencial para contornar o erro.

Se aparecer `exchange_invalid_response`, o executor recebeu uma resposta de pareamento
inválida, excessivamente aninhada ou interrompida no transporte e continua aguardando
outro convite, sem salvar a resposta nem repetir a troca.
Isso inclui timeout e reset durante a leitura após uma resposta HTTP de sucesso;
falhas antes de obter a resposta e HTTP 429/5xx mantêm a política de indisponibilidade.
Confira a API escolhida e emita um novo convite pela interface. A API pode ter consumido
o anterior: se houver um cadastro sem conexão correspondente, revogue-o pela interface.
Não apague o estado privado para tentar recuperar o pareamento.

Todos os convites recusados são lembrados durante a execução atual: alternar o
arquivo entre convites antigos não os reenvia. Essa memória não persiste após
reiniciar; forneça um convite novo. `--invite` explícito mantém precedência sobre
o arquivo, inclusive quando já foi recusado.

`executor_credential_save_failed` indica que uma identidade válida foi recebida,
mas sua gravação local falhou. A CLI encerra sem repetir o convite e sem abrir
o canal autenticado. Preserve o estado privado, inclusive eventual
`credential.tmp`: ele pode conter uma credencial e não deve ser compartilhado
ou promovido automaticamente. Confira espaço e acesso ao diretório e o cadastro
na interface antes de reiniciar. Se a identidade não puder ser recuperada com
segurança, revogue o cadastro sem conexão e emita outro convite explicitamente.
Não apague journal, marcador ou arquivos de trabalho para tentar parear.
Antes de publicar `credential.json`, o executor descarrega o buffer e sincroniza
com `fsync` o conteúdo de `credential.tmp`. Falha nessa etapa também encerra com
`executor_credential_save_failed`, preservando a identidade anterior e o temporário;
o reinício exige a reconciliação descrita abaixo. Vale também para `computer`.
Isso não garante durabilidade completa após perda de energia: o diretório não é
sincronizado, e caches do dispositivo/filesystem continuam fora desta verificação.

`executor_credential_pending` bloqueia o reinício se `credential.tmp` existir
(inclusive vazio, parcial, diretório ou link quebrado) ou não puder ser inspecionado.
A recusa ocorre antes de preparar recursos, abrir o journal, trocar convites ou
conectar, mesmo com `credential.json` válido. O temporário pode ser a única cópia
de uma identidade já emitida; não é adotado, apagado ou sobrescrito pelo executor.
Preserve uma cópia privada de todo o estado, confira o cadastro na interface e
reconcilie explicitamente qual identidade deve permanecer antes de reiniciar.
Se o cadastro pendente for revogado, seu temporário ainda precisa ser separado
do caminho ativo pelo operador, após preservação da evidência; emitir outro
convite sozinho não libera a recusa. Não altere journal, marcador ou arquivos de
trabalho. Esta proteção não detecta perda conjunta de credencial e temporário
nem substitui backup ou garante persistência após perda de energia.
Se o temporário surgir durante a chamada de pareamento, a criação exclusiva
recusa a colisão com `executor_credential_save_failed`, sem truncar o arquivo
ou seguir links. No Windows, usa `CreateFileW` com `CREATE_NEW` e
`FILE_FLAG_OPEN_REPARSE_POINT`, inclusive para links sem destino. A API pode
já ter consumido o convite; preserve a evidência
e reconcilie o cadastro antes de reiniciar. Isso não protege contra adulteração
arbitrária do diretório por outro processo após a abertura do arquivo.

Um arquivo `--invite-file` ilegível ou com JSON que excede o parser mantém a
espera por um convite válido. Substitua-o pelo convite emitido pela interface;
`--invite` explícito continua tendo precedência sobre o arquivo.

Se aparecer `exchange_kind_mismatch`, o convite retornou uma identidade de tipo
diferente do modo iniciado (`personal` ou `computer`). O executor não salva essa
credencial e aguarda outro convite: emita um convite do tipo correto e substitua
o arquivo indicado por `--invite-file` (ou reinicie com o novo `--invite`).
O convite anterior pode ter sido consumido; revogue o cadastro sem conexão na
interface.

No reinício, `executor_kind_mismatch` recusa um tipo salvo incompatível ou ausente
antes de enviar a credencial à API. Reinicie no modo original (`personal` ou
`computer`); para outro pareamento, use um diretório `--state` separado.
A credencial é preservada e nenhum novo convite é consumido. A checagem não
repara credenciais incompatíveis já salvas.

O WebSocket autenticado de comandos e o canal de visualização também recusam
redirecionamentos, inclusive na mesma origem. Uma resposta 3xx preserva a credencial;
o canal de comandos tenta novamente somente a API configurada, sem novo pareamento.
Corrija o endpoint/proxy para servir a conexão diretamente.

Pareamento HTTP, comandos e visualização conectam diretamente à URL configurada,
sem descobrir proxies no ambiente ou nas configurações do sistema. Variáveis como
`HTTP_PROXY`, `HTTPS_PROXY`, `WS_PROXY` e `WSS_PROXY` não encaminham esses canais.
O executor exige `websockets>=15,<16` para desativar proxy explicitamente; essa
faixa também é usada pelo workspace e pela imagem do computador próprio.
Não há opção de proxy de saída: a API precisa ser alcançável diretamente pela
máquina que executa o conector/runtime. Um reverse proxy já presente na URL da
API continua possível, servindo HTTP e WebSocket sem redirecionamento. Isso não
contorna proxies transparentes, resolução DNS ou a confiança TLS do sistema.

## O que a abelha pode fazer

| Recurso | Concessão | Política padrão |
| --- | --- | --- |
| Listar/ler arquivos nas pastas concedidas | Pasta | Permitir |
| Escrever/apagar/converter | Pasta + Escrita | Perguntar |
| Terminal (`personal_shell`) | Terminal | Perguntar |
| Navegador com perfil Bees separado | Navegador | abrir/observar: Permitir; clicar/digitar: Perguntar |
| Publicar arquivo como entrega | Pasta | Permitir |

Caminhos usam o alias da pasta (`Documentos/relatorio.pdf`). O conector confere novamente a concessão recebida com cada comando e recusa caminhos que saiam das raízes, inclusive por links. **Terminal tem o alcance da sua conta do sistema** — conceda somente quando necessário.

## Revogar

Desmarque a concessão da abelha ou clique **Revogar conector**. A revogação impede novos comandos imediatamente e encerra a conexão; comandos não enviados expiram sem efeito.

Se `credential.json` existir com conteúdo inválido ou estiver ilegível, o executor
encerra com `executor_credential_invalid`, antes de preparar recursos ou abrir o
journal. Isso inclui JSON `null`, aninhamento que excede o parser, arquivo truncado,
identidade incompleta e link quebrado. O arquivo, o convite pendente e o journal são preservados; nenhum convite
substitui automaticamente uma credencial inválida. Preserve o estado privado e
restaure o arquivo original de um backup confiável. Sem esse backup, a recuperação
do cadastro exige avaliação humana; apagar o estado não é recuperação suportada.
A ausência completa do arquivo ainda permite a primeira instalação. Esta checagem
não detecta perda total do estado, rollback nem corrupção arbitrária do journal.
