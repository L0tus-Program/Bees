# Aparência e leitura da interface

No cabeçalho, **Aparência** oferece **Sistema**, **Clara** e **Escura**, inclusive
na tela de primeiro acesso. Sistema acompanha a preferência do dispositivo e suas
mudanças enquanto a página está aberta. Uma escolha explícita prevalece sobre o
sistema. Idioma e aparência são independentes; trocar a aparência conserva a edição
de formulários e não inicia tarefa, geração ou instalação.

A preferência fica no armazenamento local deste navegador para a origem da
instalação. Outras abas da mesma origem recebem a mudança; outro navegador ou
endereço mantém sua própria escolha. Não é preferência por abelha e não entra no
banco/exportação. Se o navegador bloquear o salvamento, a escolha funciona na aba
e a interface informa que não conseguiu conservá-la. Valor ausente ou inválido
volta a Sistema. Nenhuma senha, mensagem ou configuração de modelo é armazenada
junto dessa escolha.

As paletas usam variáveis CSS explícitas, inclusive no build de produção. Controles
nativos recebem o esquema correspondente; a preferência de redução de movimento
remove animações e transições. Os seletores têm nome acessível e escopo anunciado;
foco de teclado e estados de aviso continuam visíveis. A paleta clara é conservada com dois ajustes de contraste no estado de preparo
e no autor da mensagem. A paleta escura cobre texto, superfícies, formulários e
estados.

## Evidência e limites

Os testes de interface verificam Sistema/alteração do dispositivo, escolha
explícita, leitura inicial, sincronização entre abas, limpeza/valor inválido,
armazenamento bloqueado, retirada de listeners e nomes nos três idiomas.

A prova Chromium usa o build de produção, instalação própria sem worker ou modelo
e somente consultas após login. Verifica cores realmente renderizadas, contraste
de texto visível, pt-BR/en/es em390/1280, edição preservada na proposta de rotina,
foco de teclado, redução de movimento, reload e outra aba. A navegação pelos12
painéis em390/1280 e claro/escuro integrou69 verificações e67 medições de tela,
com oito capturas e nenhuma falha de contraste nos textos visíveis medidos.
Treze tabelas canônicas conservaram seus hashes; nenhuma mutação de negócio ou
chamada de modelo ocorreu. O reflow640CSSpx é uma
verificação adicional; não é uma medição do zoom da interface do navegador.

A leitura visual e a medição de texto não constituem auditoria WCAG completa ou
aceite com leitor de tela humano. Conteúdo de app/VM remoto tem sua própria
acessibilidade; esta aparência não demonstra controle daquele ambiente. O aceite
amplo permanece em [BEES-025.3](https://github.com/L0tus-Program/Bees/issues/88).

## Verificação ponta a ponta

`npm --prefix apps/web run build && npm --prefix apps/web run e2e` sobe API, worker
e um modelo simulado por HTTP, e percorre no Chromium o primeiro acesso, a criação
da abelha, conversa e publicação de arquivo. Em cada tela roda o axe-core
(WCAG 2.1 A/AA) em 390 e 1280 px e, na conversa, em pt-BR/en/es; falha com qualquer
violação séria/crítica, rolagem horizontal ou geração a mais. O próprio script
confere antes que o axe acusa falhas conhecidas. O job **Interface ponta a ponta /
Chromium** do CI publica capturas e `e2e.json`.

As configurações usam os mesmos tokens da conversa em todas as 13 seções. No
desktop, a navegação agrupa perfil/modelo/memórias, trabalho/entregas e
acesso/execução; no celular, um seletor nomeado oferece todas as seções. A seleção
no desktop leva o foco ao título do conteúdo; o seletor móvel mantém seu foco
para permitir navegação nativa pelo teclado. A navegação e o conteúdo têm rolagem
própria quando necessário, sem esticar a página.

O roteiro verifica salvamento real de Perfil e Modelo, foco de teclado, as 13
seções em 390/1280 px, Modelo também em inglês/espanhol, e Perfil/Modelo/Rotinas/
Ferramentas no tema escuro, incluindo a confirmação de revogação (sem confirmar
a ação) e o contraste do botão destrutivo em hover. Acrescenta 768 px com janela baixa e 1920 px nas
configurações. A edição de configuração não deve acrescentar gerações às três
esperadas do fluxo de conversa/arquivo. Os cenários percorrem os estados da
instalação de teste; não cobrem todos os erros, conteúdos ou integrações externas,
nem substituem revisão humana com leitor de tela.
