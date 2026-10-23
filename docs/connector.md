# Conector da máquina pessoal

O conector deixa a abelha trabalhar no seu computador **somente** nas pastas e recursos que você conceder. Ele usa o mesmo executor do computador próprio ([ADR 0004](adr/0004-computer-runtime.md)).

## Instalar e parear

1. Na aba **Computador** da abelha, em **Máquina pessoal**, clique **Conectar máquina pessoal**. Um convite de uso único (15 minutos) aparece com o comando sugerido.
2. Na sua máquina (Windows, Linux ou macOS, Python 3.12+):

   ```sh
   pip install "bees-executor[browser,documents]"   # ou a partir de apps/executor do checkout
   python -m playwright install chromium            # somente se for conceder navegador
   bees-executor personal --api http://127.0.0.1:8080 --invite <convite> \
     --root Documentos=~/Documents --root Downloads=~/Downloads
   ```

   Cada `--root Alias=Caminho` declara uma pasta compartilhável. Nada fora delas fica acessível.
3. De volta à interface, o conector aparece **conectado**. Para cada abelha, marque as pastas concedidas e, se quiser, **Escrita**, **Terminal** e **Navegador**; clique **Salvar concessão**.
4. Instale/conceda o pacote **Máquina pessoal (conector)** (`bees.personal`) em **Ferramentas**.

A credencial fica em `executor-state/credential.json` (permissão restrita). Reiniciar o conector reconecta sem novo convite.

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
