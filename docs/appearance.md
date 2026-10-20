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
