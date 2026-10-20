# Caso verificável do Writer

BEES-015.1 (#45) fixa o caso `bees-writer-001`: produzir um plano de horta didática
editável em ODT e uma cópia PDF, com orçamento, comparação, recomendação e limites.
O alvo continua sendo **LibreOffice Writer 25.2.3**, Debian 13 amd64, do kit
[`linux-desktop-v1`](guest-image.md). O caso e o validador não tornam o computador
provisionável, não acrescentam aplicativos ao catálogo e não implementam o adapter
de BEES-012.3 (#35), tarefa Bees ou handoff de BEES-015.2 (#46).

## Entrada e resultado esperado

A [entrada HTML própria](../scripts/fixtures/writer-case.html) contém somente dados
fictícios. É uma referência reproduzível, não uma pesquisa de preços ou documento
de cliente. O trabalho representativo consiste em estruturar o relatório, conferir
os valores, salvar o documento editável e exportar sua versão de leitura.

| Item da alternativa A | Quantidade | Unitário | Subtotal |
| --- | ---: | ---: | ---: |
| Kit de ferramentas | 6 | R$ 50,00 | R$ 300,00 |
| Mudas | 40 | R$ 5,00 | R$ 200,00 |
| Sacos de terra | 15 | R$ 30,00 | R$ 450,00 |
| Total | | | R$ 950,00 |

O teto é R$ 1.200,00; as alternativas B e C custam R$ 1.120,00 e R$ 1.040,00. A escolha
A deixa R$ 250,00 de margem. O relatório precisa dizer que a comparação considera
somente os valores fictícios fornecidos e que preços, qualidade, frete e
disponibilidade precisam ser conferidos antes de qualquer compra. A URL
`https://example.org/bees-writer-001` é um identificador de fixture, sem consulta
de rede ou afirmação de que é fonte de mercado.

Operações do caso: criar/editar texto e tabela no workspace próprio, salvar ODT,
reabrir/salvar e exportar PDF. Compras, envio de mensagens, arquivos pessoais,
macros, objetos incorporados e compatibilidade geral com Office ficam fora.
Publicar como artefato Bees deverá reutilizar o armazenamento/metadados de
BEES-018, depois da integração do adapter; este script não cria outro pipeline.

## Reprodução de laboratório

Use um diretório novo e dados próprios. Primeiro registre a versão real do app.
Os comandos abaixo são operações explícitas do desenvolvedor no laboratório,
não comandos de onboarding ou ferramentas autorizadas ao modelo:

```sh
soffice --version
soffice -env:UserInstallation=file:///CAMINHO/ABSOLUTO/NOVO/perfil \
  --headless --infilter='HTML (StarWriter)' \
  --convert-to odt:writer8 --outdir /CAMINHO/ABSOLUTO/NOVO/saida \
  scripts/fixtures/writer-case.html
soffice -env:UserInstallation=file:///CAMINHO/ABSOLUTO/NOVO/perfil \
  --headless --convert-to pdf:writer_pdf_Export \
  --outdir /CAMINHO/ABSOLUTO/NOVO/saida \
  /CAMINHO/ABSOLUTO/NOVO/saida/writer-case.odt
uv run --locked python scripts/validate-writer-case.py \
  --odt /CAMINHO/ABSOLUTO/NOVO/saida/writer-case.odt \
  --pdf /CAMINHO/ABSOLUTO/NOVO/saida/writer-case.pdf
```

O filtro de importação explícito importa: o filtro automático pode criar um
**Writer/Web** com MIME `application/vnd.oasis.opendocument.text-web`, mesmo com
extensão `.odt`. Esse arquivo é recusado. Reabrir o ODT no Writer e salvá-lo em outro
diretório novo precisa conservar os conteúdos esperados. Não sobrescreva uma
entrega anterior para repetir o teste. Registre versões, comandos, códigos de
saída e hashes dos arquivos próprios; não publique perfil do app ou logs brutos.

O validador não inicia o Writer. PDF exige `pdfinfo` e `pdftotext` já disponíveis
num laboratório POSIX; ausência gera `writer_pdf_parser_unavailable`. Não instala
pacotes nem usa rede. O kit não passa a exigir Poppler em produção por causa deste
instrumento de desenvolvimento.

## O que a validação prova

O [validador](../scripts/validate-writer-case.py) verifica conteúdo do ZIP/ODF,
MIME, manifesto, CRC, XML, parágrafos e tabela. Confere as linhas e valores do
orçamento, além das seções, totais, comparação, recomendação e limites. No PDF,
parsers reais precisam reconhecer páginas e extrair os conteúdos e linhas da
tabela. Um nome/extensão ou cabeçalho isolado não basta.

Arquivos vazios, truncados, formato incorreto, DTD/entidades, scripts declarados,
membros incompatíveis, caminhos perigosos e conteúdo criptografado são recusados.
Arquivo estruturalmente válido com conteúdo ausente/incorreto fica `partial`,
com identificadores fechados de critérios faltantes, nunca sucesso vazio. O teste
de valor alterado mantém os demais marcadores, mas ainda recusa a tabela incoerente.

Limites: 16 MiB por arquivo, ZIP32 sem comentários e até 128 membros/32 MiB expandidos;
2 MiB por membro/XML, 30.000 nós e profundidade 40; PDF com até 8 páginas e 1 MiB de texto.
Cada parser PDF tem prazo de 8 s, CPU de 4 s, memória virtual de 512 MiB e saída de 1 MiB.
Os arquivos são lidos sem extração do ODT, sem shell e sem expor texto, paths
privados ou stderr do parser no relatório. Isso não é sandbox de documentos nem
autorização para abrir arquivos arbitrários recebidos de terceiros no Writer.

`content_candidate` identifica arquivo com os conteúdos previstos. O código de saída 0 da CLI
significa que **os dois arquivos passaram nessas verificações**, não que a tarefa
Bees ou o suporte do app foram aceitos. O relatório mantém revisão visual humana
pendente, execução Bees não demonstrada e versão do app não coletada pela CLI.
Metadados do documento nunca comprovam qual app executou o trabalho. A composição
de laboratório pode registrar a versão observada separadamente; até um
`supported_lab_candidate` continua exigindo revisão humana e prova do fluxo Bees.

## Revisão humana

Conferir legibilidade de títulos, linhas e valores, sem corte ou sobreposição;
editabilidade real do ODT; equivalência do PDF; contas e limite orçamentário;
recomendação coerente com as entradas; ausência de fontes/preços inventados e
limitações explícitas. Marcadores de texto não demonstram raciocínio correto ou
qualidade editorial. Documento escaneado/imagem sem texto e todos os formatos
Office não fazem parte deste caso.

## Evidência cloud e pendências — 17/10/2026

Foram gerados ODT e PDF próprios, reaberto/salvo o ODT e renderizado o PDF. Os dois
parsers passaram, incluindo acentos e orçamento; a inspeção visual do agente
encontrou tabela e seções legíveis numa página, sem corte visível. O primeiro
Writer/Web foi corretamente recusado antes da correção do importador. Os testes
de corrupção/limites/segredo do diagnóstico estão em
[`test_writer_case.py`](../scripts/tests/test_writer_case.py).

Essa primeira execução usou **LibreOfficeDev 26.8.0.0.alpha0**, diferente do alvo
25.2.3. O snapshot Debian fixado estava bloqueado naquele momento; o checkpoint
abaixo substitui essa limitação de versão e acesso ao snapshot.

## Evidência no Writer suportado — 19/10/2026

O snapshot passou a responder, e a receita existente produziu os alvos
`verification-image` e `installed-lab`, preservando a base fixada, assinaturas APT
e hashes de `InRelease`. O `installed-lab` instalou os pacotes numa base mínima com
`RUN --network=none`; os dois smoke tests também rodaram com `--network none`.
As dez recusas controladas, o replay do instalador e a geração de PDF pelo usuário
restrito passaram. Não foi necessário alterar dependências ou pins da receita.

Nesse laboratório, **LibreOffice 25.2.3.2 520(Build:2)** importou a fixture pelo
filtro `HTML (StarWriter)`, salvou ODT, exportou PDF, reabriu/salvou o ODT em outro
diretório e exportou novamente. Os quatro arquivos pertenciam ao UID10001.
Os dois pares passaram pelo validador: 32 parágrafos, uma tabela, todos os
marcadores e valores presentes e PDF de uma página. A versão foi observada pelo
comando do app; a CLI de validação continua sem inferir versão por metadados.

| Arquivo inicial | Bytes | SHA256 |
| --- | ---: | --- |
| ODT | 27.726 | `a4d293a776381d739426aeacb527524c3628dc568987333083b6a00a2f8fc93e` |
| PDF | 46.392 | `fcccb0b51880ff561ccb253ecc42573178b1139ee3f05d52878771f1ec32f36b` |

As duas renderizações foram inspecionadas pelo agente e são idênticas: título,
tabela, contas, comparação, recomendação e limites ficaram legíveis, sem corte
ou sobreposição visíveis. Isso é evidência de laboratório para revisão, não
revisão humana aceita. Perfis e documentos gerados permanecem privados e fora
do controle de versão.

A issue continua **Entrega Parcial**: revisão humana, execução pelo adapter/tarefa
Bees e ambiente real ainda estão pendentes. O download do ISO completo permaneceu
bloqueado por proxy 403 em `cdimage.debian.org`; isso não impediu o teste do payload
autenticado já construído. Não há declaração de VM, handoff, modelo real ou
ampliação do catálogo neste teste.
