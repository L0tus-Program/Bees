# Avisos e fontes da imagem do computador

O código Bees nesta imagem usa Apache-2.0; LICENSE e NOTICE ficam em
`/usr/share/doc/bees`. Os demais componentes conservam suas licenças.
Os avisos dos pacotes Ubuntu ficam em `/usr/share/doc/<pacote>/copyright`;
os do Python, Chromium e Playwright permanecem nas respectivas instalações.

Antes de redistribuir uma imagem pronta, o mantenedor responsável pela release deve:

1. Fixar o digest da imagem efetivamente construída e exportar o inventário de
   pacotes, versões e avisos, incluindo os herdados da imagem-base.
2. Identificar as obrigações de cada componente. Para pacotes que exigem fonte
   correspondente, obter a versão exata e seus patches/scripts de construção.
3. Disponibilizar esses fontes pelo mecanismo exigido pela licença junto da
   distribuição e registrar onde podem ser obtidos. Um link genérico para um
   repositório não substitui essa conferência.

Fontes oficiais para a conferência: [Ubuntu](https://packages.ubuntu.com/),
[Launchpad](https://launchpad.net/ubuntu),
[Playwright](https://github.com/microsoft/playwright) e
[LibreOffice](https://www.libreoffice.org/about-us/licenses/).
Confira a versão concreta; esta lista não é uma oferta de fontes correspondente
a uma imagem ainda não publicada.

O build local não faz upload para registry. Não remova avisos do sistema nem
marque como publicada uma imagem cujo inventário e fontes não foram conferidos.
