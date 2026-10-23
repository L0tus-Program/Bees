"""Executor remoto do Bees: computador próprio da abelha e conector pessoal.

Recebe somente comandos já autorizados pelo plano de controle, executa com
limites locais e devolve recibos correlacionados. Não lê o banco do serviço nem
recebe credenciais de modelos.
"""

PROTOCOL = 1
VERSION = "0.1.0"
