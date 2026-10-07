# Kit de instalação do computador Linux

BEES-011 prepara `linux-desktop-v1` como um kit local: ISO oficial Debian intacto e payload separado de aplicações. O catálogo continua planejado, com `usable` e `provisionable` falsos. Esta etapa não entrega uma VM inicializável já configurada nem integra execução ao modelo.

O kit é uma ferramenta de desenvolvimento e operação. A instalação gráfica do computador pelo usuário final continua no escopo de [ambientes](environments.md) e BEES-028; executar scripts no terminal não será o onboarding final.

## Composição e confiança

O download começa no servidor Debian por HTTPS. Redirecionamento só é aceito para `ftp.acc.umu.se` e seus hosts de entrega, mirror [registrado pelo Debian](https://www.debian.org/CD/http-ftp/), preservando o mesmo caminho/versionamento, HTTPS e limites. Troca de caminho/versão, porta, credenciais, query ou outro domínio é recusada. O destino de entrega não substitui a assinatura e o hash fixados.

A versão amd64 do Debian, tamanho e SHA256 do ISO, fingerprint da chave pública e imagem de build são fixados no repositório. O builder verifica a assinatura OpenPGP do arquivo de checksums e o checksum do ISO antes de finalizar o kit. A chave pública é âncora de confiança local; uma chave entregue junto de um download não pode substituir essa âncora automaticamente. Consulte a [verificação oficial do Debian](https://www.debian.org/CD/verify).

O payload contém aplicações e dependências obtidas de snapshots oficiais autenticados pelo APT: XFCE, Chromium, LibreOffice Writer e Python do guest. O [serviço snapshot do Debian](https://snapshot.debian.org/) conserva versões de pacotes por data; atualizar o snapshot é uma mudança explícita de receita e exige nova validação. O Python do guest não executa a API, o core ou o helper Windows do Bees.

O snapshot inicial é `20261005T000000Z`. APT verifica assinaturas com o keyring Debian da base e o builder também confere hashes fixados de `InRelease`. Downloads de pacotes do laboratório usam HTTP com autenticação APT; a exceção `Check-Valid-Until: no` vale somente para esses snapshots históricos, sem desabilitar assinaturas. A receita e seus pins precisam concordar antes do build. A instalação offline não altera repositórios globais do guest.

Inventários registram versões, origens, checksums, tamanhos e avisos de licença. ISO e payload não recebem chaves de modelos, banco, cofre, cookies, arquivos pessoais, convites ou bootstrap de uma VM. Hashes do pacote local detectam alterações relativamente ao inventário; a assinatura Debian autentica o material upstream correspondente. O kit local do Bees ainda não é assinado para distribuição. Licença do Bees e revisão de avisos continuam requisitos de publicação, conforme o [README](../README.md).

## Instalação e alcance

O ISO conserva o instalador oficial. Não contém preseed, particionamento automático ou senha compartilhada; selecionar disco e confirmar a instalação permanecem decisões humanas no [instalador Debian](https://www.debian.org/releases/trixie/amd64/ch06s03.en.html). O kit não formata ou monta discos do hospedeiro.

O payload é aplicado explicitamente a um Debian13 amd64 já instalado. Seu instalador precisa de root dentro desse guest para instalar pacotes e criar a conta restrita `abelha`, UID/GID10001, sem sudo e com senha bloqueada. Uma conta conflitante não é substituída. O workspace dedicado não representa isolamento por si: limites de discos, memória, rede e acesso pessoal pertencem ao provisionador e devem ser comprovados na VM real.

O instalador aceita somente `--confirm-guest-install`, com payload extraído numa árvore pertencente a root e sem escrita por grupo/outros, inclusive nos ancestrais. Links, arquivos especiais, hardlinks e arquivos adicionais são recusados antes do APT. Não executar um instalador root a partir de diretório gravável pela abelha. O artefato local do Bees pressupõe o checkout/operador confiável; inventário e checksum não concedem confiança a scripts recebidos de terceiros.

Instalação incompleta não pode ser apresentada como pronta. Replay verifica a instalação conhecida; configuração inesperada ou estado parcial exigem reconciliação explícita. As dependências do payload são locais durante essa etapa; nenhum endpoint ou credencial do Bees é aceito como parâmetro do instalador.

## Validação e trabalho restante

### Comandos do desenvolvedor

No Windows, com Docker Desktop Linux e `.venv` do projeto:

```powershell
.venv/Scripts/python.exe scripts/build-guest-image.py
.venv/Scripts/python.exe scripts/build-guest-image.py --verify-only
```

O resultado fica em `dist/guest-image/linux-desktop-v1`, ignorado pelo Git. `--iso-file caminho` permite reutilizar um ISO baixado, mantendo as mesmas verificações de assinatura, tamanho e hash. Build não substitui um kit existente, não inicia VM e não instala nada no Windows. Staging e lock próprios impedem publicar um kit parcial; uma interrupção do processo pode exigir revisar o staging/lock, preservando artefatos anteriores.

Para comprovar instalação offline a partir de uma base limpa:

```powershell
docker build --platform linux/amd64 --target installed-lab -t bees-guest-installed-lab:local -f runtime/linux-desktop/Dockerfile .
docker run --rm --network none bees-guest-installed-lab:local
```

O alvo `verification-image` testa recusas controladas e replay. Ambos os alvos são laboratórios, separados da imagem da aplicação em `compose.yaml`. O builder usa somente a receita pública permitida, sem transmitir dados, configuração privada ou dependências locais como contexto.

O laboratório Docker usa somente dados e volumes descartáveis, sem modo privilegiado, dispositivos ou socket do hospedeiro. Executar o Writer como a conta restrita pode comprovar produção de documento e persistência do workspace após recriação. Disponibilidade do executável Chromium comprova apenas o pacote instalado. Esses resultados não demonstram boot de VM, desktop interativo, sandbox do navegador, ponte autenticada ou isolamento de rede.

Para disponibilizar o computador pela interface, ainda são necessários provisionador separado com concessão própria, identidade/bootstrap por VM, ponte autenticada, rede filtrada fora do guest, armazenamento persistente e testes reais de boot, recursos, acesso visual e recuperação. A aprovação para geração textual não concede esses acessos. Não instalar Hyper-V/libvirt/QEMU nem alterar redes automaticamente.

### Evidência do checkpoint — 06/10/2026

Os mesmos 35 testes do builder também passaram no Linux, em container sem rede.

Build real e `--verify-only` aprovados: ISO Debian13.7.0 original de 792.723.456 bytes, manifesto assinado pela chave CD fixada; payload offline de 387.782.226 bytes, 418 pacotes e 861 arquivos inventariados. SHA256 do payload: `302ad0db9f2b8627213fcbcc2d26e7d0047921a781006a656c4b0d72a5f734a7`. Kit total aproximadamente1,18GB. Inventários conservam versões, avisos e referências upstream de licença; publicação continua pendente.

O alvo `installed-lab` instalou o payload em base Debian mínima com `RUN --network=none`. Writer25.2.3 produziu PDF como UID10001; Chromium154.0.8037.92 respondeu à consulta de versão. Replay conservou o PDF. Um volume exclusivo descartável conservou o mesmo hash após remoção/recriação do container; leitura final ocorreu como UID10001, sem capacidades ou rede. Nenhuma VM foi criada e não se executou Chromium com `--no-sandbox`.

Dez recusas controladas: falta de confirmação, usuário sem root, pacote adicional, checksum alterado, árvore gravável, dono inesperado, estado parcial, GID/UID conflitantes e falha real simulada do APT com retry recusado. A falha do APT3 ao usar `--no-download` com .deb local foi corrigida oferecendo apenas métodos locais `file/copy/store`, sem fontes remotas e sem métodos HTTP/HTTPS na instalação.

Suíte completa Windows: 932 testes Python aprovados, 27 skips de plataforma; web:358 testes, lint/tipos/build aprovados. Builder:35 casos de origem/assinatura/mirror, links/traversal/permissões, limites e preservação/staging. Ruff e formato aprovados. Logs, kit e documento de laboratório ficam em `data/validation` e `dist/guest-image`, ignorados pelo Git. A instalação padrão permanece saudável no schema7, sem dados de teste, mudanças no hipervisor ou rede do Windows.
