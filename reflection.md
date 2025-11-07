### Reflection

1. What new knowledge you learned through this experiment? If you have used LLMs, any specific insights/knowledge/skills your acquired through LLMs.

+ The experiment makes us more familiar with the workflow of transportation protocol, shows the detailed logic corresponding to the concepts we learnt in the lecture before. It also provides us with many chance to use the library methods that has never used before.

2. What are the trade-offs in transport layer protocol design?

+ We have considered use normal `ACK` at first because it’s more simple to manage and stable enough. But at last, considering the efficiency issue, we decided to use `SACK` instead. Meanwhile, we originally tried to implement mimicking jitter and reordering of network in the `ex_sender.py` and `ex_receiver.py`. But later we find its too complicated compared to directly use `netem` tools in linux environment. And `netem` can also implement simulation of real network environment better.