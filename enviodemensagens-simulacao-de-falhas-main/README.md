 Simulação de Envio de Mensagens com Falhas

Aplicação desenvolvida em **Python** com o objetivo de **simular a comunicação entre dois pontos (nós)** em uma rede, permitindo analisar o comportamento do sistema diante de **falhas, atrasos e perda de conectividade**.

---

 Objetivo do Projeto

Este projeto foi criado com o intuito de estudar e demonstrar conceitos relacionados a:

- Comunicação entre processos
- Envio e recebimento de mensagens em rede
- Tratamento de falhas em sistemas distribuídos
- Confiabilidade e resiliência na troca de mensagens

A aplicação permite simular cenários em que um dos nós perde o acesso à rede, apresenta atraso no processamento ou falha durante o envio/recebimento de mensagens.

---

 Funcionalidade Geral

- Simulação de troca de mensagens entre dois pontos
- Envio de mensagens via sockets locais
- Confirmação de entrega por meio de ACKs
- Simulação de falhas como:
  - Perda de conexão
  - Atraso no processamento
  - Queda durante o envio da mensagem
  - Duplicação de mensagens
- Visualização do estado das mensagens (pendente, enviada, entregue, falha)

---

 Conceitos Trabalhados

- Sistemas distribuídos
- Comunicação em rede
- Tratamento de falhas
- Retentativas de envio
- Controle de estados de mensagens
- Deduplicação de mensagens por identificador

---

 Tecnologias Utilizadas

- Python
- Sockets
- Tkinter (interface gráfica)
- Threads
- JSON

---

 Execução

Este projeto é executado **localmente**, abrindo duas instâncias da aplicação em portas diferentes para simular a comunicação entre dois nós.


---

 Contexto Acadêmico

Projeto desenvolvido com fins acadêmicos no curso de **Engenharia de Software**, com foco em:
- Comunicação entre sistemas
- Simulação de falhas de rede
- Confiabilidade em troca de mensagens

---


**Beatriz Rodrigues Lima**  
Estudante de Engenharia de Software  
🔗 GitHub: https://github.com/Bea-Lima07
