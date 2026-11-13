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
inválida, excessivamente aninhada ou truncada no transporte e continua aguardando
outro convite, sem salvar a resposta nem repetir a troca.
Confira a API escolhida e emita um novo convite pela interface. A API pode ter consumido
o anterior: se houver um cadastro sem conexão correspondente, revogue-o pela interface.
Não apague o estado privado para tentar recuperar o pareamento.

`executor_credential_save_failed` indica que uma identidade válida foi recebida,
mas sua gravação local falhou. A CLI encerra sem repetir o convite e sem abrir
o canal autenticado. Preserve o estado privado, inclusive eventual
`credential.tmp`: ele pode conter uma credencial e não deve ser compartilhado
ou promovido automaticamente. Confira espaço e acesso ao diretório e o cadastro
na interface antes de reiniciar. Se a identidade não puder ser recuperada com
segurança, revogue o cadastro sem conexão e emita outro convite explicitamente.
Não apague journal, marcador ou arquivos de trabalho para tentar parear.
Esta recusa vale também para o runtime `computer`; não cobre perda de energia
ou falhas de durabilidade que não produzam erro de gravação.

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
