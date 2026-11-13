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

O estado é vinculado à URL da API usada no pareamento. Se outro `--api` for
informado no reinício, `executor_api_mismatch` encerra o executor antes de enviar
a credencial. O arquivo é preservado; reinicie com a URL original. Uma barra
final é equivalente, mas outras diferenças de URL são recusadas. Para outra
instalação, use outro `--state` e faça um pareamento próprio. Estado sem vínculo
válido também é bloqueado: não edite nem apague a credencial para contornar o erro.

Se aparecer `exchange_invalid_response`, o executor recebeu uma resposta de pareamento
inválida ou truncada no transporte e continua aguardando outro convite, sem salvar
a resposta nem repetir a troca.
Confira a API escolhida e emita um novo convite pela interface. A API pode ter consumido
o anterior: se houver um cadastro sem conexão correspondente, revogue-o pela interface.
Não apague o estado privado para tentar recuperar o pareamento.

Se aparecer `exchange_kind_mismatch`, o convite retornou uma identidade de tipo
diferente do modo iniciado (`personal` ou `computer`). O executor não salva essa
credencial e aguarda outro convite: emita um convite do tipo correto e substitua
o arquivo indicado por `--invite-file` (ou reinicie com o novo `--invite`).
O convite anterior pode ter sido consumido; revogue o cadastro sem conexão na
interface. A checagem não repara credenciais incompatíveis já salvas.

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
